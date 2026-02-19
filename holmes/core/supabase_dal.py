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

# Maximum total rows to fetch from KRR scans, regardless of number of clusters
# This prevents unbounded fetches when querying many clusters
MAX_KRR_TOTAL_FETCH_ROWS = 2000

ISSUES_TABLE = "Issues"
GROUPED_ISSUES_TABLE = "GroupedIssues"
EVIDENCE_TABLE = "Evidence"
RUNBOOKS_TABLE = "HolmesRunbooks"
SESSION_TOKENS_TABLE = "AuthTokens"
HOLMES_STATUS_TABLE = "HolmesStatus"
HOLMES_TOOLSET = "HolmesToolsStatus"
SCANS_META_TABLE = "ScansMeta"
SCANS_RESULTS_TABLE = "ScansResults"
SCHEDULED_PROMPTS_RUNS_TABLE = "ScheduledPromptsRuns"
HOLMES_RESULTS_TABLE = "HolmesResults"

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


class RunStatus(str, Enum):
    PENDING = "pending"
    PULLED = "pulled"
    RUNNING = "running"
    FAILED = "failed"
    FAILED_NO_RETRY = "failed_no_retry"
    COMPLETED = "completed"


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
        clusters: Optional[List[str]] = None,
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
            clusters: List of cluster names to query. If None, queries current cluster only.
                      Use ["*"] to query all clusters in the account.

        Returns:
            List of recommendations sorted by the specified metric
        """
        if not self.enabled:
            return []

        # Determine which clusters to query
        if clusters is None:
            target_clusters = [self.cluster]
        elif clusters == ["*"]:
            target_clusters = None  # Will query all via single request
        else:
            target_clusters = clusters

        # Step 1: Fetch scan metadata for all target clusters in one query
        meta_query = (
            self.client.table(SCANS_META_TABLE)
            .select("cluster_id, scan_id")
            .eq("account_id", self.account_id)
            .eq("latest", True)
        )
        if target_clusters is not None:
            meta_query = meta_query.in_("cluster_id", target_clusters)

        scans_meta_response = meta_query.execute()

        if not scans_meta_response.data:
            logging.warning("No scan metadata found for KRR")
            return None

        # Build cluster_id -> scan_id mapping
        cluster_scan_pairs: List[Tuple[str, str]] = [
            (row["cluster_id"], row["scan_id"]) for row in scans_meta_response.data
        ]

        if not cluster_scan_pairs:
            return None

        # Step 2: Fetch results using OR filter for (cluster_id, scan_id) pairs
        # PostgREST syntax: or=(and(cluster_id.eq.X,scan_id.eq.Y),and(...))
        or_conditions = ",".join(
            f"and(cluster_id.eq.{cluster_id},scan_id.eq.{scan_id})"
            for cluster_id, scan_id in cluster_scan_pairs
        )

        query = (
            self.client.table(SCANS_RESULTS_TABLE)
            .select("*")
            .eq("account_id", self.account_id)
            .or_(or_conditions)
        )

        if namespace:
            query = query.eq("namespace", namespace)
        if name_pattern:
            query = query.like("name", name_pattern)
        if kind:
            query = query.eq("kind", kind)
        if container:
            query = query.eq("container", container)

        # For priority sorting, we can use DB ordering and limit
        if sort_by == "priority":
            query = query.order("priority", desc=True).limit(limit)
            results_response = query.execute()
            return results_response.data if results_response.data else None

        # For other sort modes, fetch up to limit per cluster then sort in Python
        # Cap total fetch to prevent unbounded queries with many clusters
        total_fetch = min(limit * len(cluster_scan_pairs), MAX_KRR_TOTAL_FETCH_ROWS)
        query = query.limit(total_fetch)
        results_response = query.execute()

        if not results_response.data:
            return None

        all_results = results_response.data

        if len(all_results) <= 1:
            return all_results

        # Sort by calculated savings (descending)
        results_with_savings = [
            (result, calculate_krr_savings(result, sort_by)) for result in all_results
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
        clusters: Optional[List[str]] = None,
        include_external: bool = True,
        finding_type: FindingType = FindingType.CONFIGURATION_CHANGE,
    ) -> Optional[List[Dict]]:
        """
        Fetch issues/changes metadata with multi-cluster support.

        Args:
            start_datetime: Start time boundary in RFC3339 format
            end_datetime: End time boundary in RFC3339 format
            limit: Maximum number of results to return
            workload: Filter by workload name (exact match)
            ns: Filter by namespace (exact match)
            clusters: List of cluster names to query. If None, queries current cluster only.
                      Use ["*"] to query all clusters in the account.
            include_external: If True, also include external changes (not associated with
                             any k8s cluster, e.g., LaunchDarkly changes). Default True.
            finding_type: Type of finding to fetch (CONFIGURATION_CHANGE or ISSUE)

        Returns:
            List of issues/changes metadata or None if no data found
        """
        if not self.enabled:
            return []

        try:
            base_select = (
                "id",
                "title",
                "subject_name",
                "subject_namespace",
                "subject_type",
                "description",
                "starts_at",
                "ends_at",
                "cluster",
            )

            # Build the list of clusters to query (single query using IN filter)
            if clusters == ["*"]:
                # Query all clusters - if include_external, no cluster filter needed
                # Otherwise exclude "external"
                query = (
                    self.client.table(ISSUES_TABLE)
                    .select(*base_select)
                    .eq("account_id", self.account_id)
                    .gte("creation_date", start_datetime)
                    .lte("creation_date", end_datetime)
                    .eq("finding_type", finding_type.value)
                )
                if not include_external:
                    query = query.neq("cluster", "external")
            else:
                # Build cluster list for IN filter
                target_clusters = clusters if clusters else [self.cluster]
                if include_external:
                    target_clusters = target_clusters + ["external"]

                query = (
                    self.client.table(ISSUES_TABLE)
                    .select(*base_select)
                    .eq("account_id", self.account_id)
                    .in_("cluster", target_clusters)
                    .gte("creation_date", start_datetime)
                    .lte("creation_date", end_datetime)
                    .eq("finding_type", finding_type.value)
                )

            # Apply workload/namespace filters (only affect non-external results,
            # but external rows won't match these anyway as they lack k8s context)
            if workload:
                query = query.eq("subject_name", workload)
            if ns:
                query = query.eq("subject_namespace", ns)

            # Order by starts_at descending and apply limit in DB
            query = query.order("starts_at", desc=True).limit(limit)

            res = query.execute()

            if not res.data:
                return None

            return res.data

        except Exception:
            logging.exception("Supabase error while retrieving change data")
            return None

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

    def has_scheduled_prompt_definitions(self) -> bool:
        """
        Check if the account has any scheduled prompt definitions.
        Returns True if count > 0, False otherwise.
        """
        if not self.enabled:
            return False

        try:
            res = (
                self.client.table("ScheduledPromptsDefinitions")
                .select("id", count="exact")
                .eq("account_id", self.account_id)
                .limit(1)
                .execute()
            )

            count = res.count if hasattr(res, "count") else 0
            return count > 0
        except Exception:
            logging.exception(
                "Supabase error while checking scheduled prompt definitions",
                exc_info=True,
            )
            return False

    def claim_scheduled_prompt_run(self, holmes_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None

        try:
            res = self.client.rpc(
                "claim_scheduled_prompt_run",
                {
                    "_account_id": self.account_id,
                    "_cluster_name": self.cluster,
                    "_holmes_id": holmes_id,
                },
            ).execute()

            if not res.data:
                return None

            row = res.data[0] if isinstance(res.data, list) else res.data
            # supabase returns empty row if no data found
            if not row.get("id"):
                return None

            return row
        except Exception:
            logging.exception(
                "Supabase error while claiming scheduled prompt run",
                exc_info=True,
            )
            return None

    def update_run_status(
        self, run_id: str, status: RunStatus, msg: Optional[str] = None
    ) -> bool:
        if not self.enabled:
            logging.info(
                "Robusta store not initialized. Skipping updating scheduled prompt run status."
            )
            return False

        status_str = status.value

        try:
            update_data = {
                "status": status_str,
                "last_heartbeat_at": datetime.now().isoformat(),
            }
            if msg is not None:
                update_data["msg"] = msg

            (
                self.client.table(SCHEDULED_PROMPTS_RUNS_TABLE)
                .update(update_data)
                .eq("id", run_id)
                .eq("account_id", self.account_id)
                .execute()
            )

            logging.debug(f"Updated run {run_id} status to {status}")
            return True
        except Exception as e:
            logging.exception(
                f"Error updating scheduled prompt run status: {e}", exc_info=True
            )
            return False

    def finish_scheduled_prompt_run(
        self,
        status: RunStatus,
        result: Dict,
        run_id: str,
        scheduled_prompt_definition_id: Optional[str],
        version: str,
        metadata: Optional[dict],
    ) -> bool:
        if not self.enabled:
            logging.info(
                "Robusta store not initialized. Skipping finishing scheduled prompt run."
            )
            return False

        if status not in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.FAILED_NO_RETRY,
        ):
            logging.error(
                "finish_scheduled_prompt_run received invalid status %s", status
            )
            return False

        try:
            self.client.rpc(
                "finish_scheduled_prompt_run",
                {
                    "_cluster_name": self.cluster,
                    "_account_id": self.account_id,
                    "_status": status.value,
                    "_result": result,
                    "_scheduled_prompt_run_id": run_id,
                    "_scheduled_prompt_definition_id": scheduled_prompt_definition_id,
                    "_version": version,
                    "_metadata": metadata,
                },
            ).execute()
            return True
        except Exception:
            logging.exception(
                "Supabase error while finishing scheduled prompt run",
                exc_info=True,
            )
            return False
