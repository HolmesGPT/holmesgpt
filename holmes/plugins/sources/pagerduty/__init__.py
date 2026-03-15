import logging
from typing import List, Optional

import requests  # type: ignore

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import SourcePlugin
from holmes.utils.markdown_utils import markdown_to_plain_text


class PagerDutySource(SourcePlugin):
    def __init__(
        self, api_key: str, user_email: str, incident_key: Optional[str] = None
    ):
        self.api_url = (
            "https://api.pagerduty.com"  # currently hard-coded, can expose it if useful
        )
        self.api_key = api_key
        self.user_email = user_email
        self.incident_key = incident_key

    def fetch_issues(self) -> List[Issue]:
        logging.info(f"Fetching issues from {self.api_url}")
        try:
            headers = {
                "Authorization": f"Token token={self.api_key}",
                "Accept": "application/vnd.pagerduty+json;version=2",
            }

            # excludes resolved
            query_params = "?statuses[]=triggered&statuses[]=acknowledged"

            if self.incident_key:
                query_params = f"{query_params}&incident_key={self.incident_key}"

            response = requests.get(
                f"{self.api_url}/incidents{query_params}", headers=headers
            )
            if response.status_code != 200:
                print(f"Got response: {response}")
                raise Exception(
                    f"Failed to get issues: {response.status_code} {response.text}"
                )
            logging.debug(f"Got response: {response}")
            response.raise_for_status()
            data = response.json()
            return [self.convert_to_issue(issue) for issue in data.get("incidents", [])]
        except requests.RequestException as e:
            raise ConnectionError("Failed to fetch data from PagerDuty.") from e

    def fetch_issue(self, id: str) -> Optional[Issue]:  # type: ignore
        """
        Fetch a single issue from PagerDuty using the incident ID and convert it to an Issue object.

        :param incident_id: The ID of the incident to fetch.
        :return: An Issue object if found, otherwise None.
        """
        logging.info(f"Fetching issue {id} from {self.api_url}")

        headers = {
            "Authorization": f"Token token={self.api_key}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }

        try:
            response = requests.get(f"{self.api_url}/incidents/{id}", headers=headers)

            if response.status_code == 404:
                logging.warning(f"Incident {id} not found.")
                return None

            if response.status_code != 200:
                logging.error(
                    f"Failed to get issue: {response.status_code} {response.text}"
                )
                raise Exception(
                    f"Failed to get issue: {response.status_code} {response.text}"
                )

            logging.debug(f"Got response: {response.json()}")
            incident_data = response.json().get("incident")

            if incident_data:
                issue = self.convert_to_issue(incident_data)
                issue.description = self._enrich_with_alert_details(
                    id, headers, issue.description
                )
                return issue
            else:
                logging.warning(f"No incident data found for {id}.")
                return None

        except requests.RequestException as e:
            logging.error(f"Connection error while fetching issue {id}: {e}")
            raise ConnectionError("Failed to fetch data from PagerDuty.") from e

    def convert_to_issue(self, source_issue):
        return Issue(
            id=source_issue["id"],
            name=source_issue["summary"],
            source_type="pagerduty",
            source_instance_id=self.api_url,
            url=f"{source_issue['html_url']}",
            description=source_issue.get("description"),
            raw=source_issue,
        )

    def _enrich_with_alert_details(
        self, incident_id: str, headers: dict, base_description: Optional[str]
    ) -> Optional[str]:
        """Fetch alerts for an incident and append their body details to the description.

        PagerDuty incidents created by integrations (Grafana, Datadog, etc.) store
        rich context (labels, annotations, firing info) in the alert body rather
        than in the incident description.  This method fetches those details via
        GET /incidents/{id}/alerts and appends them so the LLM has full context.
        """
        try:
            resp = requests.get(
                f"{self.api_url}/incidents/{incident_id}/alerts",
                headers=headers,
            )
            resp.raise_for_status()
            parts = [base_description] if base_description else []
            for alert in resp.json().get("alerts", []):
                body_details = (alert.get("body") or {}).get("details") or {}
                for key, value in body_details.items():
                    if value and str(value).strip():
                        parts.append(f"{key}: {value}")
            return "\n\n".join(parts) if parts else base_description
        except Exception:
            logging.debug(
                f"Failed to fetch alert details for incident {incident_id}",
                exc_info=True,
            )
            return base_description

    def write_back_result(self, issue_id: str, result_data: LLMResult) -> None:
        logging.info(f"Writing back result to issue {issue_id}")
        if not self.user_email:
            raise Exception(
                "When using --update mode, --pagerduty-user-email must be provided"
            )

        try:
            url = f"{self.api_url}/incidents/{issue_id}/notes"
            headers = {
                "Authorization": f"Token token={self.api_key}",
                "Content-Type": "application/json",
                "From": self.user_email,
            }
            comment = markdown_to_plain_text(result_data.result)
            comment_data = {
                "note": {
                    "content": f"Automatic AI Investigation by HolmesGPT:\n\n{comment}"
                }
            }
            response = requests.post(url, json=comment_data, headers=headers)
            response.raise_for_status()
            data = response.json()
            logging.debug(f"Comment added to issue {issue_id}: {data}")
        except requests.RequestException as e:
            if e.response is not None:
                logging.error(
                    f"Failed to write back result to PagerDuty: {e}; {e.response.text}"
                )
            else:
                logging.error(f"Failed to write back result to PagerDuty: {e}")
            raise


# Run with:
# poetry run python3 -m holmes.plugins.sources.pagerduty <api-key> <user-email>
if __name__ == "__main__":
    import sys

    pd_source = PagerDutySource(api_key=sys.argv[1], user_email=sys.argv[2])
    issues = pd_source.fetch_issues()
    for issue in issues:
        pd_source.write_back_result(issue.id, LLMResult(result="This is a test"))
        print(issue)
