import logging
from typing import List

import requests  # type: ignore

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.interfaces import SourcePlugin


class GitHubSource(SourcePlugin):
    def __init__(self, url: str, owner: str, repository: str, pat: str, query: str):
        self.url = url
        self.owner = owner
        self.repository = repository
        self.pat = pat
        self.query = query

    def fetch_issues(self) -> List[Issue]:
        logging.info(
            f"Fetching All issues from {self.url} for repository {self.owner}/{self.repository}"
        )
        try:
            data = []
            url = f"{self.url}/search/issues"
            headers = {
                "Authorization": f"token {self.pat}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            params = {"per_page": "100"}
            default_q = f"repo:{self.owner}/{self.repository}"
            params["q"] = f"{default_q} {self.query}"
            while url:
                response = requests.get(url=url, headers=headers, params=params)
                if response.status_code != 200:
                    raise Exception(
                        f"Failed to get issues:{response.status_code} {response.text}"
                    )
                logging.info(f"Got {response}")
                response.raise_for_status()
                data.extend(response.json().get("items", []))
                links = response.headers.get("Link", "")
                url = None  # type: ignore
                for link in links.split(","):
                    if 'rel="next"' in link:
                        url = link.split(";")[0].strip()[1:-1]
            return [self.convert_to_issue(issue) for issue in data]
        except requests.RequestException as e:
            raise ConnectionError("Failed to fetch data from GitHub.") from e

    def convert_to_issue(self, github_issue):
        return Issue(
            id=str(github_issue["number"]),
            name=github_issue["title"],
            source_type="github",
            source_instance_id=f"{self.owner}/{self.repository}",
            url=github_issue["html_url"],
            raw=github_issue,
        )

    def write_back_result(self, issue_id: str, result_data: LLMResult) -> None:
        url = f"{self.url}/repos/{self.owner}/{self.repository}/issues/{issue_id}/comments"
        headers = {
            "Authorization": f"token {self.pat}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = requests.post(
            url=url,
            json={
                "body": f"Automatic AI Investigation by Robusta:\n\n{result_data.result}\n"
            },
            headers=headers,
        )

        response.raise_for_status()
        data = response.json()
        logging.debug(f"Posted comment to issue #{issue_id} at {data['html_url']}")
