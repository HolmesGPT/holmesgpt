import base64
import binascii
import gzip
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import sentry_sdk
import yaml  # type: ignore
from cachetools import TTLCache  # type: ignore
from postgrest._sync import request_builder as supabase_request_builder
from postgrest._sync.request_builder import SyncQueryRequestBuilder
from postgrest.base_request_builder import QueryArgs
from postgrest.exceptions import APIError as PGAPIError
from postgrest.types import ReturnMethod
from pydantic import BaseModel
from supabase import create_client
from supabase.lib.client_options import ClientOptions

from holmes.common.env_vars import (
    ROBUSTA_ACCOUNT_ID,
    ROBUSTA_CONFIG_PATH,
    STORE_API_KEY,
    STORE_EMAIL,
    STORE_PASSWORD,
    STORE_URL,
)
from holmes.core.resource_instruction import (
    ResourceInstructionDocument,
    ResourceInstructions,
)
from holmes.core.truncation.dal_truncation_utils import (
    truncate_evidences_entities_if_necessary,
)
from holmes.plugins.runbooks import RobustaRunbookInstruction
from holmes.utils.definitions import RobustaConfig
from holmes.utils.env import get_env_replacement
from holmes.utils.global_instructions import Instructions
from holmes.utils.krr_utils import calculate_krr_savings

SUPABASE_TIMEOUT_SECONDS = int(os.getenv("SUPABASE_TIMEOUT_SECONDS", 3600))

ISSUES_TABLE = "Issues"
GROUPED_ISSUES_TABLE = "GroupedIssues"
EVIDENCE_TABLE = "Evidence"
RUNBOOKS_TABLE = "HolmesRunbooks"
SESSION_TOKENS_TABLE = "AuthTokens"
HOLMES_STATUS_TABLE = "HolmesStatus"
HOLMES_TOOLSET = "HolmesToolsStatus"
SCANS_META_TABLE = "ScansMeta"
SCANS_RESULTS_TABLE = "ScansResults"

ENRICHMENT_BLACKLIST = ["text_file", "graph", "ai_analysis", "holmes"]
ENRICHMENT_BLACKLIST_SET = set(ENRICHMENT_BLACKLIST)


logging.info("Patching supabase_request_builder.pre_select")
original_pre_select = supabase_request_builder.pre_select


def pre_select_patched(*args, **kwargs):
    query_args: QueryArgs = original_pre_select(*args, **kwargs)
    if not query_args.json:
        query_args = QueryArgs(
            query_args.method, query_args.params, query_args.headers, None
        )

    return query_args


supabase_request_builder.pre_select = pre_select_patched


class FindingType(str, Enum):
    ISSUE = "issue"
    CONFIGURATION_CHANGE = "configuration_change"


class RobustaToken(BaseModel):
    store_url: str
    api_key: str
    account_id: str
    email: str
    password: str


class SupabaseDnsException(Exception):
    def __init__(self, error: Exception, url: str):
        message = (
            f"\n{error.__class__.__name__}: {error}\n"
            f"Error connecting to <{url}>\n"
            "This is often due to DNS issues or firewall policies - to troubleshoot run in your cluster:\n"
            f"curl -I {url}\n"
        )
        super().__init__(message)


class SupabaseDal:
    def __init__(self, cluster: str):
        self.enabled = self.__init_config()
        self.cluster = cluster
        if not self.enabled:
            logging.debug(
                "Not connecting to Robusta platform - robusta token not provided - using ROBUSTA_AI will not be possible"
            )
            return
        logging.info(
            f"Initializing Robusta platform connection for account {self.account_id}"
        )
        options = ClientOptions(postgrest_client_timeout=SUPABASE_TIMEOUT_SECONDS)
        sentry_sdk.set_tag("db_url", self.url)
        self.client = create_client(self.url, self.api_key, options)  # type: ignore
        self.user_id = self.sign_in()
        ttl = int(os.environ.get("SAAS_SESSION_TOKEN_TTL_SEC", "82800"))  # 23 hours
        self.patch_postgrest_execute()
        self.token_cache = TTLCache(maxsize=1, ttl=ttl)
        self.lock = threading.Lock()

    def patch_postgrest_execute(self):
        logging.info("Patching postgres execute")

        # This is somewhat hacky.
        def execute_with_retry(_self):
            try:
                return self._original_execute(_self)
            except PGAPIError as exc:
                message = exc.message or ""
                if exc.code == "PGRST301" or "expired" in message.lower():
                    # JWT expired. Sign in again and retry the query
                    logging.error(
                        "JWT token expired/invalid, signing in to Supabase again"
                    )
                    self.sign_in()
                    # update the session to the new one, after re-sign in
                    _self.session = self.client.postgrest.session
                    return self._original_execute(_self)
                else:
                    raise

        self._original_execute = SyncQueryRequestBuilder.execute
        SyncQueryRequestBuilder.execute = execute_with_retry

    @staticmethod
    def __load_robusta_config() -> Optional[RobustaToken]:
        config_file_path = ROBUSTA_CONFIG_PATH
        env_ui_token = os.environ.get("ROBUSTA_UI_TOKEN")
        if env_ui_token:
            # token provided as env var
            try:
                decoded = base64.b64decode(env_ui_token)
                return RobustaToken(**json.loads(decoded))
            except binascii.Error:
                raise Exception(
                    "binascii.Error encountered. The Robusta UI token is not a valid base64."
                )
            except json.JSONDecodeError:
                raise Exception(
                    "json.JSONDecodeError encountered. The Robusta UI token could not be parsed as JSON after being base64 decoded."
                )

        if not os.path.exists(config_file_path):
            logging.debug(f"No robusta config in {config_file_path}")
            return None

        logging.info(f"loading config {config_file_path}")
        with open(config_file_path) as file:
            yaml_content = yaml.safe_load(file)
            config = RobustaConfig(**yaml_content)
            for conf in config.sinks_config:
                if "robusta_sink" in conf.keys():
                    token = conf["robusta_sink"].get("token")
                    if not token:
                        raise Exception(
                            "No robusta token provided to Holmes.\n"
                            "Please set a valid Robusta UI token.\n "
                            "See https://holmesgpt.dev/ai-providers/ for instructions."
                        )
                    env_replacement_token = get_env_replacement(token)
                    if env_replacement_token:
                        token = env_replacement_token

                    if "{{" in token:
                        raise ValueError(
                            "The robusta token configured for Holmes appears to be a templating placeholder (e.g. `{ env.UI_SINK_TOKEN }`).\n "
                            "Ensure your Helm chart or environment variables are set correctly.\n "
                            "If you store the token in a secret, you must also pass "
                            "the environment variable ROBUSTA_UI_TOKEN to Holmes.\n "
                            "See https://holmesgpt.dev/data-sources/builtin-toolsets/robusta/ for instructions."
                        )
                    try:
                        decoded = base64.b64decode(token)
                        return RobustaToken(**json.loads(decoded))
                    except binascii.Error:
                        raise Exception(
                            "binascii.Error encountered. The robusta token provided to Holmes is not a valid base64."
                        )
                    except json.JSONDecodeError:
                        raise Exception(
                            "json.JSONDecodeError encountered. The Robusta token provided to Holmes could not be parsed as JSON after being base64 decoded."
                        )
        return None

    def __init_config(self) -> bool:
        # trying to load the supabase connection parameters from the robusta token, if exists
        # if not, using env variables as fallback
        robusta_token = self.__load_robusta_config()
        if robusta_token:
            self.account_id = robusta_token.account_id
            self.url = robusta_token.store_url
            self.api_key = robusta_token.api_key
            self.email = robusta_token.email
            self.password = robusta_token.password
        else:
            self.account_id = ROBUSTA_ACCOUNT_ID
            self.url = STORE_URL
            self.api_key = STORE_API_KEY
            self.email = STORE_EMAIL
            self.password = STORE_PASSWORD

        # valid only if all store parameters are provided
        return all([self.account_id, self.url, self.api_key, self.email, self.password])

    def sign_in(self) -> str:
        logging.info("Supabase dal login")
        try:
            res = self.client.auth.sign_in_with_password(
                {"email": self.email, "password": self.password}
            )
            if not res.session:
                raise ValueError("Authentication failed: no session returned")
            if not res.user:
                raise ValueError("Authentication failed: no user returned")
            self.client.auth.set_session(
                res.session.access_token, res.session.refresh_token
            )
            self.client.postgrest.auth(res.session.access_token)
            return res.user.id
        except Exception as e:
            error_msg = str(e).lower()
            if any(
                dns_indicator in error_msg
                for dns_indicator in [
                    "temporary failure in name resolution",
                    "name resolution",
                    "dns",
                    "name or service not known",
                    "nodename nor servname provided",
                ]
            ):
                raise SupabaseDnsException(e, self.url) from e
            raise

    def get_resource_recommendation(
        self,
        limit: int = 10,
        sort_by: str = "cpu_total",
        namespace: Optional[str] = None,
        name_pattern: Optional[str] = None,
        kind: Optional[str] = None,
        container: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """
        Fetch top N resource recommendations with optional filters and sorting.

        Args:
            limit: Maximum number of recommendations to return (default: 10)
            sort_by: Field to sort by potential savings. Options:
                - "cpu_total": Total CPU savings (requests + limits)
                - "memory_total": Total memory savings (requests + limits)
                - "cpu_requests": CPU requests savings
                - "memory_requests": Memory requests savings
                - "cpu_limits": CPU limits savings
                - "memory_limits": Memory limits savings
                - "priority": Use the priority field from the scan
            namespace: Filter by Kubernetes namespace (exact match)
            name_pattern: Filter by workload name (supports SQL LIKE pattern, e.g., '%app%')
            kind: Filter by Kubernetes resource kind (e.g., Deployment, StatefulSet, DaemonSet, Job)
            container: Filter by container name (exact match)

        Returns:
            List of recommendations sorted by the specified metric
        """
        if not self.enabled:
            return []

        scans_meta_response = (
            self.client.table(SCANS_META_TABLE)
            .select("*")
            .eq("account_id", self.account_id)
            .eq("cluster_id", self.cluster)
            .eq("latest", True)
            .execute()
        )
        if not len(scans_meta_response.data):
            logging.warning("No scan metadata found for latest krr scan")
            return None

        scan_id = scans_meta_response.data[0]["scan_id"]

        query = (
            self.client.table(SCANS_RESULTS_TABLE)
            .select("*")
            .eq("account_id", self.account_id)
            .eq("cluster_id", self.cluster)
            .eq("scan_id", scan_id)
        )

        if namespace:
            query = query.eq("namespace", namespace)
        if name_pattern:
            query = query.like("name", name_pattern)
        if kind:
            query = query.eq("kind", kind)
        if container:
            query = query.eq("container", container)

        # For priority sorting, we can use the database's order
        if sort_by == "priority":
            query = query.order("priority", desc=True).limit(limit)

        scans_results_response = query.execute()

        if not len(scans_results_response.data):
            return None

        results = scans_results_response.data

        if len(results) <= 1:
            return results

        # If sorting by priority, we already ordered and limited in the query
        if sort_by == "priority":
            return results

        # Sort by calculated savings (descending)
        results_with_savings = [
            (result, calculate_krr_savings(result, sort_by)) for result in results
        ]
        results_with_savings.sort(key=lambda x: x[1], reverse=True)

        return [result for result, _ in results_with_savings[:limit]]

    def get_issues_metadata(
        self,
        start_datetime: str,
        end_datetime: str,
        limit: int = 100,
        workload: Optional[str] = None,
        ns: Optional[str] = None,
        cluster: Optional[str] = None,
        finding_type: FindingType = FindingType.CONFIGURATION_CHANGE,
    ) -> Optional[List[Dict]]:
        if not self.enabled:
            return []
        if not cluster:
            cluster = self.cluster
        try:
            query = (
                self.client.table(ISSUES_TABLE)
                .select(
                    "id",
                    "title",
                    "subject_name",
                    "subject_namespace",
                    "subject_type",
                    "description",
                    "starts_at",
                    "ends_at",
                )
                .eq("account_id", self.account_id)
                .eq("cluster", cluster)
                .gte("creation_date", start_datetime)
                .lte("creation_date", end_datetime)
                .limit(limit)
            )

            query = query.eq("finding_type", finding_type.value)
            if workload:
                query.eq("subject_name", workload)
            if ns:
                query.eq("subject_namespace", ns)

            res = query.execute()
            if not res.data:
                return None

        except Exception:
            logging.exception("Supabase error while retrieving change data")
            return None

        logging.debug(
            "Change history metadata for %s-%s workload %s in ns %s: %s",
            start_datetime,
            end_datetime,
            workload,
            ns,
            res.data,
        )

        return res.data

    def unzip_evidence_file(self, data):
        try:
            evidence_list = json.loads(data.get("data", "[]"))
            if not evidence_list:
                return data

            evidence = evidence_list[0]
            raw_data = evidence.get("data")

            if evidence.get("type") != "gz" or not raw_data:
                return data

            # Strip "b'...'" or 'b"..."' markers if present
            if raw_data.startswith("b'") and raw_data.endswith("'"):
                raw_data = raw_data[2:-1]
            elif raw_data.startswith('b"') and raw_data.endswith('"'):
                raw_data = raw_data[2:-1]

            gz_bytes = base64.b64decode(raw_data)
            decompressed = gzip.decompress(gz_bytes).decode("utf-8")

            evidence["data"] = decompressed
            data["data"] = json.dumps([evidence])
            return data

        except Exception:
            logging.exception(f"Unknown issue unzipping gz finding: {data}")
            return data

    def extract_relevant_issues(self, evidence):
        data = [
            enrich
            for enrich in evidence.data
            if enrich.get("enrichment_type") not in ENRICHMENT_BLACKLIST_SET
        ]

        unzipped_files = [
            self.unzip_evidence_file(enrich)
            for enrich in evidence.data
            if enrich.get("enrichment_type") == "text_file"
            or enrich.get("enrichment_type") == "alert_raw_data"
        ]

        data.extend(unzipped_files)
        return data

    def get_issue_from_db(self, issue_id: str, table: str) -> Optional[Dict]:
        issue_response = (
            self.client.table(table).select("*").filter("id", "eq", issue_id).execute()
        )
        if len(issue_response.data):
            return issue_response.data[0]
        return None

    def get_issue_data(self, issue_id: Optional[str]) -> Optional[Dict]:
        # TODO this could be done in a single atomic SELECT, but there is no
        # foreign key relation between Issues and Evidence.
        if not issue_id:
            return None
        if not self.enabled:  # store not initialized
            return None
        issue_data = None
        try:
            issue_data = self.get_issue_from_db(issue_id, ISSUES_TABLE)
            if issue_data and issue_data["source"] == "prometheus":
                logging.debug("Getting alert %s from GroupedIssuesTable", issue_id)
                # This issue will have the complete alert duration information
                issue_data = self.get_issue_from_db(issue_id, GROUPED_ISSUES_TABLE)

        except Exception:  # e.g. invalid id format
            logging.exception("Supabase error while retrieving issue data")
            return None
        if not issue_data:
            return None
        evidence = (
            self.client.table(EVIDENCE_TABLE)
            .select("*")
            .eq("issue_id", issue_id)
            .not_.in_("enrichment_type", ENRICHMENT_BLACKLIST)
            .execute()
        )
        relevant_evidence = self.extract_relevant_issues(evidence)
        truncate_evidences_entities_if_necessary(relevant_evidence)

        issue_data["evidence"] = relevant_evidence

        # build issue investigation dates
        started_at = issue_data.get("starts_at")
        if started_at:
            dt = datetime.fromisoformat(started_at)

            # Calculate timestamps
            start_timestamp = dt - timedelta(minutes=10)
            end_timestamp = dt + timedelta(minutes=10)

            issue_data["start_timestamp"] = start_timestamp.strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            issue_data["end_timestamp"] = end_timestamp.strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            issue_data["start_timestamp_millis"] = int(
                start_timestamp.timestamp() * 1000
            )
            issue_data["end_timestamp_millis"] = int(end_timestamp.timestamp() * 1000)

        return issue_data

    def get_runbook_catalog(self) -> Optional[List[RobustaRunbookInstruction]]:
        if not self.enabled:
            return None

        try:
            res = (
                self.client.table(RUNBOOKS_TABLE)
                .select("*")
                .eq("account_id", self.account_id)
                .eq("subject_type", "RunbookCatalog")
                .execute()
            )
            if not res.data:
                return None

            instructions = []
            for row in res.data:
                id = row.get("runbook_id")
                symptom = row.get("symptoms")
                title = row.get("subject_name")
                if not symptom:
                    logging.warning("Skipping runbook with empty symptom: %s", id)
                    continue
                instructions.append(
                    RobustaRunbookInstruction(id=id, symptom=symptom, title=title)
                )
            return instructions
        except Exception:
            logging.exception("Failed to fetch RunbookCatalog", exc_info=True)
            return None

    def get_runbook_content(
        self, runbook_id: str
    ) -> Optional[RobustaRunbookInstruction]:
        if not self.enabled:
            return None

        res = (
            self.client.table(RUNBOOKS_TABLE)
            .select("*")
            .eq("account_id", self.account_id)
            .eq("subject_type", "RunbookCatalog")
            .eq("runbook_id", runbook_id)
            .execute()
        )
        if not res.data or len(res.data) != 1:
            return None

        row = res.data[0]
        id = row.get("runbook_id")
        symptom = row.get("symptoms")
        title = row.get("subject_name")
        raw_instruction = row.get("runbook").get("instructions")
        # TODO: remove in the future when we migrate the table data
        if isinstance(raw_instruction, list) and len(raw_instruction) == 1:
            instruction = raw_instruction[0]
        elif isinstance(raw_instruction, list) and len(raw_instruction) > 1:
            # not currently used, but will be used in the future
            instruction = "\n - ".join(raw_instruction)
        elif isinstance(raw_instruction, str):
            # not supported by the current UI, but will be supported in the future
            instruction = raw_instruction
        else:
            # in case the format is unexpected, convert to string
            logging.error(
                f"Unexpected runbook instruction format for runbook_id={runbook_id}: {raw_instruction}"
            )
            instruction = str(raw_instruction)

        return RobustaRunbookInstruction(
            id=id, symptom=symptom, instruction=instruction, title=title
        )

    def get_resource_instructions(
        self, type: str, name: Optional[str]
    ) -> Optional[ResourceInstructions]:
        if not self.enabled or not name:
            return None

        res = (
            self.client.table(RUNBOOKS_TABLE)
            .select("runbook")
            .eq("account_id", self.account_id)
            .eq("subject_type", type)
            .eq("subject_name", name)
            .execute()
        )
        if res.data:
            instructions = res.data[0].get("runbook").get("instructions")
            documents_data = res.data[0].get("runbook").get("documents")
            documents = []

            if documents_data:
                for document_data in documents_data:
                    url = document_data.get("url", None)
                    if url:
                        documents.append(ResourceInstructionDocument(url=url))
                    else:
                        logging.warning(
                            f"Unsupported runbook for subject_type={type} / subject_name={name}: {document_data}"
                        )

            return ResourceInstructions(instructions=instructions, documents=documents)

        return None

    def get_global_instructions_for_account(self) -> Optional[Instructions]:
        if not self.enabled:
            return None

        try:
            res = (
                self.client.table(RUNBOOKS_TABLE)
                .select("runbook")
                .eq("account_id", self.account_id)
                .eq("subject_type", "Account")
                .execute()
            )

            if res.data:
                instructions = res.data[0].get("runbook").get("instructions")
                return Instructions(instructions=instructions)
        except Exception:
            logging.exception("Failed to fetch global instructions", exc_info=True)

        return None

    def create_session_token(self) -> str:
        token = str(uuid4())
        self.client.table(SESSION_TOKENS_TABLE).insert(
            {
                "account_id": self.account_id,
                "user_id": self.user_id,
                "token": token,
                "type": "HOLMES",
            },
            returning=ReturnMethod.minimal,  # must use this, because the user cannot read this table
        ).execute()
        return token

    def get_ai_credentials(self) -> Tuple[str, str]:
        if not self.enabled:
            raise Exception(
                "You're trying to use ROBUSTA_AI, but Cannot get credentials for ROBUSTA_AI. Store not initialized."
            )

        with self.lock:
            session_token = self.token_cache.get("session_token")
            if not session_token:
                session_token = self.create_session_token()
                self.token_cache["session_token"] = session_token

        return self.account_id, session_token

    def get_workload_issues(self, resource: dict, since_hours: float) -> List[str]:
        if not self.enabled or not resource:
            return []

        cluster = resource.get("cluster")
        if not cluster:
            logging.debug("Missing workload cluster for issues.")
            return []

        since: str = (datetime.now() - timedelta(hours=since_hours)).isoformat()

        svc_key = f"{resource.get('namespace', '')}/{resource.get('kind', '')}/{resource.get('name', '')}"
        logging.debug(f"getting issues for workload {svc_key}")
        try:
            res = (
                self.client.table(ISSUES_TABLE)
                .select("id, creation_date, aggregation_key")
                .eq("account_id", self.account_id)
                .eq("cluster", cluster)
                .eq("service_key", svc_key)
                .gte("creation_date", since)
                .order("creation_date")
                .execute()
            )

            if not res.data:
                return []

            issue_dict = dict()
            for issue in res.data:
                issue_dict[issue.get("aggregation_key")] = issue.get("id")

            unique_issues: list[str] = list(issue_dict.values())

            res = (
                self.client.table(EVIDENCE_TABLE)
                .select("data, enrichment_type")
                .in_("issue_id", unique_issues)
                .not_.in_("enrichment_type", ENRICHMENT_BLACKLIST)
                .execute()
            )

            relevant_issues = self.extract_relevant_issues(res)
            truncate_evidences_entities_if_necessary(relevant_issues)
            return relevant_issues

        except Exception:
            logging.exception("failed to fetch workload issues data", exc_info=True)
            return []

    def upsert_holmes_status(self, holmes_status_data: dict) -> None:
        if not self.enabled:
            logging.info(
                "Robusta store not initialized. Skipping upserting holmes status."
            )
            return

        updated_at = datetime.now().isoformat()
        try:
            (
                self.client.table(HOLMES_STATUS_TABLE)
                .upsert(
                    {
                        "account_id": self.account_id,
                        "updated_at": updated_at,
                        **holmes_status_data,
                    },
                    on_conflict="account_id, cluster_id",
                )
                .execute()
            )
        except Exception as error:
            logging.error(
                f"Error happened during upserting holmes status: {error}", exc_info=True
            )

        return None

    def sync_toolsets(self, toolsets: list[dict], cluster_name: str) -> None:
        if not toolsets:
            logging.warning("No toolsets were provided for synchronization.")
            return

        if not self.enabled:
            logging.info(
                "Robusta store not initialized. Skipping sync holmes toolsets."
            )
            return

        provided_toolset_names = [toolset["toolset_name"] for toolset in toolsets]

        try:
            self.client.table(HOLMES_TOOLSET).upsert(
                toolsets, on_conflict="account_id, cluster_id, toolset_name"
            ).execute()

            logging.info("Toolsets upserted successfully.")

            self.client.table(HOLMES_TOOLSET).delete().eq(
                "account_id", self.account_id
            ).eq("cluster_id", cluster_name).not_.in_(
                "toolset_name", provided_toolset_names
            ).execute()

            logging.info("Toolsets synchronized successfully.")

        except Exception as e:
            logging.exception(
                f"An error occurred during toolset synchronization: {e}", exc_info=True
            )
