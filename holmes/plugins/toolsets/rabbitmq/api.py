import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import backoff
import requests  # type: ignore
from pydantic import BaseModel
from requests.auth import HTTPBasicAuth  # type: ignore

# --- Enums and Pydantic Models (Mostly Unchanged) ---


class ClusterConnectionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class RabbitMQClusterConfig(BaseModel):
    id: str = "rabbitmq"  # must be unique
    management_url: str  # e.g., http://rabbitmq-service:15672
    username: Optional[str] = None
    password: Optional[str] = None
    request_timeout_seconds: int = 30
    verify_certs: bool = True

    # For internal use
    connection_status: Optional[ClusterConnectionStatus] = None
    connection_error: Optional[str] = None


class Partition(BaseModel):
    node: str
    unreachable_nodes: List[str]  # Nodes that 'node' cannot reach


class NodeStatus(BaseModel):
    node: str
    running: bool  # Status as reported by the primary connected node


class NodeInfo(BaseModel):
    name: Optional[str] = "unknown"
    type: Optional[str] = "unknown"
    running: bool = False
    mem_used: Optional[int] = None
    mem_limit: Optional[int] = None
    mem_alarm: Optional[bool] = None
    disk_free: Optional[int] = None
    disk_free_limit: Optional[int] = None
    disk_free_alarm: Optional[bool] = None
    fd_used: Optional[int] = None
    fd_total: Optional[int] = None
    sockets_used: Optional[int] = None
    sockets_total: Optional[int] = None
    uptime: Optional[int] = None
    partitions: Optional[List[Any]] = None
    error: Optional[str] = None


class ClusterStatus(BaseModel):
    nodes: List[NodeStatus]  # Overall node running status from primary view
    network_partitions_detected: bool = False
    partition_details: List[Partition]  # Combined list of detected partitions
    raw_node_data: List[NodeInfo]  # Data from the primary connected node


# --- Helper Functions (Slight modifications) ---


def get_auth(config: RabbitMQClusterConfig) -> Optional[HTTPBasicAuth]:
    if config.username or config.password:
        return HTTPBasicAuth(
            config.username or "guest",
            config.password or "guest",
        )
    else:
        return None


def get_url(base_url: str, endpoint: str) -> str:
    """Constructs a URL using a base and an endpoint."""
    # Ensure base_url ends with '/' for urljoin to work predictably
    if not base_url.endswith("/"):
        base_url += "/"
    return urljoin(base_url, endpoint.lstrip("/"))


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=3,
    giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
    and e.response.status_code < 500,
)
def make_request(
    config: RabbitMQClusterConfig,
    method: str,
    url: str,
    params: Optional[Dict] = None,
    data: Optional[Dict] = None,
) -> Any:
    """Makes an HTTP request to the RabbitMQ Management API."""
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            auth=get_auth(config),
            params=params,
            json=data,
            timeout=config.request_timeout_seconds,
            verify=config.verify_certs,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed for {method} {url}: {e}")
        raise  # Re-raise after logging for upstream handling


def node_data_to_node_info(node_data: Dict) -> NodeInfo:
    """Converts raw node data dict to NodeInfo model."""
    return NodeInfo(**node_data)


def get_status_from_node(
    config: RabbitMQClusterConfig, target_node_name: str
) -> Optional[List[Dict]]:
    """
    Attempts to connect directly to the management API of a specific node.
    Returns the raw node list from that node's perspective, or None on failure.
    """
    try:
        # Extract hostname from node name (e.g., rabbit@hostname -> hostname)
        parts = target_node_name.split("@")
        if len(parts) != 2:
            logging.debug(
                f"Could not parse hostname from node name: {target_node_name}"
            )
            return None
        hostname = parts[1]

        # Construct the target node's management URL based on the original config's scheme/port
        parsed_original_url = urlparse(config.management_url)
        scheme = parsed_original_url.scheme or "http"
        port = parsed_original_url.port or (
            443 if scheme == "https" else 15672
        )  # Default ports
        base_target_url = f"{scheme}://{hostname}:{port}"

        target_api_url = get_url(base_target_url, "api/nodes")
        logging.debug(
            f"Attempting direct connection to node {target_node_name} via {target_api_url}"
        )

        # Use the original config for auth, timeout, cert verification etc.
        data = make_request(
            config=config,
            method="GET",
            url=target_api_url,
        )
        # Ensure data is a list as expected from /api/nodes
        if isinstance(data, list):
            return data
        else:
            logging.debug(
                f"Unexpected data format received from {target_api_url}: {type(data)}"
            )
            return None

    except requests.exceptions.RequestException as e:
        logging.debug(
            f"Failed to directly connect to node {target_node_name} via management API: {e}"
        )
        return None
    except Exception:
        logging.debug(
            f"Unexpected error trying to get status from node {target_node_name}",
            exc_info=True,
        )
        return None


# --- Main Logic Function (Updated) ---


def find_node(nodes: List[NodeInfo], node_name: str) -> Optional[NodeInfo]:
    for node in nodes:
        if node.name == node_name:
            return node
    return None


def upsert(nodes: List[NodeInfo], new_node_info: NodeInfo):
    found_index = -1
    for i, existing_node in enumerate(nodes):
        if existing_node.name == new_node_info.name:
            found_index = i
            break

    if found_index != -1:
        nodes[found_index] = new_node_info
    else:
        nodes.append(new_node_info)


def get_cluster_status(config: RabbitMQClusterConfig) -> ClusterStatus:
    """
    Gets cluster status, attempting direct connection to nodes reported as down
    to detect potential hidden partitions.
    """
    raw_nodes_data: List[Dict] = []
    try:
        url = get_url(config.management_url, "api/nodes")
        raw_nodes_data = make_request(
            config=config,
            method="GET",
            url=url,
        )
        config.connection_status = ClusterConnectionStatus.SUCCESS
        config.connection_error = None
    except Exception as e:
        logging.error(
            f"Failed to get primary cluster status from {config.management_url}: {e}"
        )
        config.connection_status = ClusterConnectionStatus.ERROR
        config.connection_error = str(e)
        # Return an empty/error status if the primary connection fails
        return ClusterStatus(
            nodes=[],
            network_partitions_detected=False,  # Cannot determine
            partition_details=[],
            raw_node_data=[],
        )

    # Process data from the primary connected node
    detected_partitions: List[Partition] = []
    primary_nodes: List[NodeInfo] = []
    nodes_reported_down: Set[str] = set()
    all_node_names_primary_view: Set[str] = set()

    for node_data in raw_nodes_data:
        node_info = node_data_to_node_info(node_data)
        if not node_info.name:
            continue

        primary_nodes.append(node_info)
        all_node_names_primary_view.add(node_info.name)

        # Store partitions reported by RabbitMQ itself
        if node_info.partitions:
            # Ensure we don't add duplicates if multiple nodes report the same partition
            partition_exists = any(
                p.node == node_info.name
                and set(p.unreachable_nodes) == set(node_info.partitions)
                for p in detected_partitions
            )
            if not partition_exists:
                detected_partitions.append(
                    Partition(
                        node=node_info.name,
                        unreachable_nodes=list(node_info.partitions),
                    )
                )

        # Keep track of nodes reported as down for later direct checks
        if not node_info.running:
            nodes_reported_down.add(node_info.name)

    # --- Enhanced Partition Detection ---
    artificially_detected_partitions: List[Partition] = []
    for node_name in nodes_reported_down:
        logging.debug(
            f"Node {node_name} reported as down by primary. Attempting direct connection."
        )
        # Try connecting directly to the node reported as down
        direct_nodes_data = get_status_from_node(config, node_name)
        if not direct_nodes_data:
            continue

        direct_nodes = [
            node_data_to_node_info(node_data) for node_data in direct_nodes_data
        ]

        logging.debug(
            f"Direct connection to {node_name} succeeded. Analyzing its cluster view."
        )
        unreachable_by_this_node: List[str] = []
        this_node = find_node(direct_nodes, node_name)

        if not this_node or not this_node.running:
            # Ignore this node if it's not running
            # if this node reports another node as running that was not considered running before, we ignore that
            # for simplicity as I expect we would get to that node by reaching out directly anyway
            logging.debug(
                f"Node {node_name} reported itself as not running upon direct connection."
            )
            continue
        else:
            # Node is running. Update the primary view with the updated node data
            logging.info(
                f"Node {node_name} reported itself as running upon direct connection. Updating primary view."
            )
            upsert(primary_nodes, this_node)

        all_node_names_direct_view: Set[str] = set()

        for node in direct_nodes:
            all_node_names_direct_view.add(node.name)  # type: ignore
            if not node.running:
                unreachable_by_this_node.append(node.name)  # type: ignore

        if unreachable_by_this_node:
            unreachable_nodes_set = set(unreachable_by_this_node)

            # Check if this specific partition view is already covered by RabbitMQ's reporting
            is_already_reported = any(
                partition.node == node_name
                and set(partition.unreachable_nodes) == unreachable_nodes_set
                for partition in detected_partitions
            )

            if not is_already_reported:
                logging.debug(
                    f"Artificially detecting partition: Node {node_name} cannot reach {unreachable_nodes_set}"
                )
                artificially_detected_partitions.append(
                    Partition(
                        node=node_name, unreachable_nodes=list(unreachable_nodes_set)
                    )
                )

        # Check for nodes present in primary view but MISSING entirely from this node's direct view
        missing_nodes = all_node_names_primary_view - all_node_names_direct_view
        if missing_nodes:
            # Combine missing nodes with those reported as down by this node
            combined_unreachable = set(unreachable_by_this_node).union(missing_nodes)
            is_already_reported = any(
                p.node == node_name and set(p.unreachable_nodes) == combined_unreachable
                for p in detected_partitions
                + artificially_detected_partitions  # Check against RMQ and our own detections
            )
            if not is_already_reported:
                logging.debug(
                    f"Artificially detecting partition: Node {node_name} cannot see (missing or down) {combined_unreachable}"
                )
                # Avoid duplicate Partition entries if already added above based only on 'running=False'
                existing_artificial = next(
                    (
                        p
                        for p in artificially_detected_partitions
                        if p.node == node_name
                    ),
                    None,
                )
                if existing_artificial:
                    existing_artificial.unreachable_nodes = list(combined_unreachable)
                else:
                    artificially_detected_partitions.append(
                        Partition(
                            node=node_name, unreachable_nodes=list(combined_unreachable)
                        )
                    )

        else:
            logging.debug(
                f"Direct connection to node {node_name} failed. Assuming it's unreachable."
            )
            pass

    # Combine RabbitMQ-reported partitions and artificially detected ones
    final_partitions = detected_partitions + artificially_detected_partitions

    # Remove potential duplicates (same node reporting same unreachable set)
    unique_partitions = []
    seen_partitions = set()
    for p in final_partitions:
        # Create a unique key: node_name + sorted tuple of unreachable nodes
        partition_key = (p.node, tuple(sorted(p.unreachable_nodes)))
        if partition_key not in seen_partitions:
            unique_partitions.append(p)
            seen_partitions.add(partition_key)

    node_statuses: List[NodeStatus] = [
        NodeStatus(node=node_info.name, running=node_info.running)  # type: ignore
        for node_info in primary_nodes
    ]

    cluster_status = ClusterStatus(
        nodes=node_statuses,  # Keep original running status view
        network_partitions_detected=True if len(unique_partitions) > 0 else False,
        partition_details=unique_partitions,
        raw_node_data=primary_nodes,  # Data from the primary node only
    )

    return cluster_status
