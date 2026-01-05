#!/usr/bin/env python3
"""
Isolates one rabbitmq pods by raising the firewall (iptables) on relevant kubernetes nodes.
This works well with local kind clusters with docker engine but has not been tested on other setups.
"""

import argparse
import subprocess
import sys
import time

from kubernetes import client, config
from kubernetes.client.rest import ApiException

NAMESPACE = "rabbitmq"
TARGET_POD_NAME = "rabbitmq-0"
PARTIAL_TARGET_POD_NAME = "rabbitmq-1"  # The pod to isolate from in partial mode
POD_LABEL_SELECTOR = "app.kubernetes.io/name=rabbitmq"
CHECK_INTERVAL_SECONDS = 5
COMMAND_RUNNER = "docker"  # Or "nerdctl" or similar if using containerd/nerdctl

try:
    try:
        config.load_kube_config()
    except config.ConfigException:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            raise Exception("Could not configure Kubernetes client.")

    v1 = client.CoreV1Api()
    print("Kubernetes client initialized successfully.")
except Exception as e:
    print(f"Error initializing Kubernetes client: {e}", file=sys.stderr)
    sys.exit(1)


def get_rabbitmq_pods_info(api_instance, namespace, label_selector):
    pods_info = []
    print(
        f"Listing pods in namespace '{namespace}' with label selector '{label_selector}'..."
    )
    try:
        pods = api_instance.list_namespaced_pod(
            namespace=namespace, label_selector=label_selector
        )
        if not pods.items:
            print(
                f"Warning: No pods found matching label selector '{label_selector}' in namespace '{namespace}'."
            )

        for pod in pods.items:
            # Filter out potential non-rabbitmq pods if selector is too broad
            if not pod.metadata.name.startswith("rabbitmq-"):
                continue

            pod_ip = pod.status.pod_ip
            node_name = pod.spec.node_name
            phase = pod.status.phase

            pods_info.append(
                {
                    "name": pod.metadata.name,
                    "ip": pod_ip,
                    "node": node_name,
                    "status": phase,
                }
            )
            print(
                f"  - Found Pod: {pod.metadata.name}, Status: {phase}, IP: {pod_ip}, Node: {node_name}"
            )

        return pods_info
    except ApiException as e:
        print(f"Error listing pods: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(
            f"An unexpected error occurred while getting pod info: {e}", file=sys.stderr
        )
        return None


def wait_for_pods_running(api_instance, namespace, label_selector, target_count=None):
    print("\n--- Waiting for all RabbitMQ pods to be Running ---")
    while True:
        all_running = True
        pods_data = get_rabbitmq_pods_info(api_instance, namespace, label_selector)

        if pods_data is None:
            print(
                f"Error retrieving pod data. Retrying in {CHECK_INTERVAL_SECONDS} seconds..."
            )
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        if not pods_data:
            print("No pods found yet. Waiting...")
            all_running = False

        if target_count is not None and len(pods_data) != target_count:
            print(
                f"Waiting for {target_count} pods, found {len(pods_data)}. Retrying..."
            )
            all_running = False

        current_statuses = []
        for pod in pods_data:
            status = pod.get("status", "Unknown")
            ip = pod.get("ip", "N/A")
            node = pod.get("node", "N/A")
            current_statuses.append(f"{pod['name']}({status}, IP:{ip}, Node:{node})")

            # Ensure pod is running and has essential info
            if status != "Running" or not ip or not node:
                all_running = False

        print(f"Current Pod Statuses: {', '.join(current_statuses) or 'None found'}")

        if all_running and pods_data:
            print("All required RabbitMQ pods are running and have IPs/Nodes.")
            return pods_data
        else:
            print(
                f"Not all pods are ready. Waiting {CHECK_INTERVAL_SECONDS} seconds..."
            )
            time.sleep(CHECK_INTERVAL_SECONDS)


def run_command_on_node(node_name, command, action):
    # Construct command based on the runner
    if COMMAND_RUNNER == "docker":
        exec_command = [COMMAND_RUNNER, "exec", node_name, "bash", "-c", command]
    elif COMMAND_RUNNER == "nerdctl":
        # nerdctl might require a different approach, often involving nsenter
        # This is a placeholder - adjust if using nerdctl or other runtimes
        # Example: Need to get container ID on the node, then use nsenter
        # This script currently assumes direct node execution via 'docker exec' which targets the node's container runtime directly
        # A more robust approach for non-Docker might involve running a privileged pod on each node.
        print(
            f"Warning: COMMAND_RUNNER '{COMMAND_RUNNER}' support is basic. Assuming direct execution.",
            file=sys.stderr,
        )
        exec_command = [
            COMMAND_RUNNER,
            "exec",
            node_name,
            "bash",
            "-c",
            command,
        ]  # May not work as intended
    else:
        print(f"Error: Unsupported COMMAND_RUNNER '{COMMAND_RUNNER}'", file=sys.stderr)
        return False

    print(f"  Executing on node '{node_name}': {' '.join(exec_command)}")
    try:
        result = subprocess.run(
            exec_command, capture_output=True, text=True, check=False, timeout=60
        )  # Increased timeout

        if result.stdout:
            print(f"    stdout: {result.stdout.strip()}")
        if result.stderr:
            stderr_lower = result.stderr.strip().lower()
            # For remove, non-existence is not a failure. Print as info.
            if action == "remove" and (
                "no chain/target/match by that name" in stderr_lower
                or "rule not found" in stderr_lower
                or "doesn't exist" in stderr_lower
            ):
                print(f"    Info (stderr): {result.stderr.strip()}")
            # Otherwise print as stderr only if command failed
            elif result.returncode != 0:
                print(f"    stderr: {result.stderr.strip()}", file=sys.stderr)
            else:  # Print as regular output if command succeeded but had stderr (e.g., warnings)
                print(f"    stderr: {result.stderr.strip()}")

        if result.returncode != 0:
            stderr_lower = result.stderr.strip().lower()
            # Check again if the error is simply that the rule doesn't exist during removal
            if action == "remove" and (
                "no chain/target/match by that name" in stderr_lower
                or "rule not found" in stderr_lower
                or "doesn't exist" in stderr_lower
            ):
                print("    Note: Rule likely did not exist or was already removed.")
                return True  # Treat as success for removal idempotency
            else:
                print(
                    f"    Command failed with exit code {result.returncode}",
                    file=sys.stderr,
                )
                return False

        # Check for "already exists" only on add/add-partial actions
        if (
            action in ["add", "add-partial"]
            and "already exists" in result.stderr.lower()
        ):
            print("    Note: iptables rule likely already exists.")

        return True

    except subprocess.TimeoutExpired:
        print(f"  Error: Command timed out on node {node_name}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(
            f"  Error: '{COMMAND_RUNNER}' command not found. Is it installed and in the PATH on the machine running this script?",
            file=sys.stderr,
        )
        # We can't proceed if the command runner isn't available locally.
        sys.exit(1)
    except Exception as e:
        print(f"  Error executing command on node {node_name}: {e}", file=sys.stderr)
        return False


def manage_iptables_rules(
    api_instance,
    namespace,
    label_selector,
    target_pod_name,
    partial_target_pod_name,
    command_runner,
    action,
):
    # Wait for pods and get their initial data
    pod_data = wait_for_pods_running(api_instance, namespace, label_selector)

    if not pod_data:
        print("Failed to get running pod data. Exiting.", file=sys.stderr)
        sys.exit(1)

    target_pod_ip = None
    partial_target_ip = (
        None  # IP of the specific pod for partial isolation (e.g., rabbitmq-1)
    )
    other_pod_ips = []  # IPs of all other rabbitmq pods (excluding the main target)
    all_nodes = set()

    print("\n--- Processing Pod Information ---")
    for pod in pod_data:
        node = pod.get("node")
        ip = pod.get("ip")
        name = pod.get("name")

        if not node or not ip or not name:
            print(
                f"Warning: Skipping pod {name} due to missing node/ip information.",
                file=sys.stderr,
            )
            continue

        print(f"Processing Pod: {name} (IP: {ip}, Node: {node})")
        all_nodes.add(node)

        if name == target_pod_name:
            target_pod_ip = ip
            print(
                f"  -> Identified Target Pod '{target_pod_name}' with IP: {target_pod_ip}"
            )
        elif name.startswith("rabbitmq-"):  # It's another rabbitmq pod
            other_pod_ips.append(ip)
            print(f"  -> Identified Other RabbitMQ Pod '{name}' with IP: {ip}")
            if name == partial_target_pod_name:
                partial_target_ip = ip
                print(
                    f"    -> Identified as Partial Isolation Target '{partial_target_pod_name}'"
                )

    if not target_pod_ip:
        print(
            f"Error: Target pod '{target_pod_name}' not found or has no IP among running pods.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not all_nodes:
        print("Error: No nodes found hosting the RabbitMQ pods.", file=sys.stderr)
        sys.exit(1)

    # Determine IPs to apply rules against based on action
    ips_to_isolate_from = []
    if action == "add":
        if not other_pod_ips:
            print(
                "Warning: No other RabbitMQ pods found to isolate from. No iptables rules will be added.",
                file=sys.stdout,
            )
        else:
            ips_to_isolate_from = other_pod_ips
    elif action == "add-partial":
        if not partial_target_ip:
            print(
                f"Warning: Partial target pod '{partial_target_pod_name}' not found or has no IP. Cannot add partial isolation rules.",
                file=sys.stderr,
            )
        else:
            ips_to_isolate_from = [partial_target_ip]
    elif action == "remove":
        # When removing, we target all potential peers in case previous state was 'add' or 'add-partial'
        if not other_pod_ips:
            print("Info: No other RabbitMQ pods found. Assuming no rules need removal.")
        else:
            # Include partial target IP as well if it exists, just to be safe during removal
            potential_peers = set(other_pod_ips)
            if partial_target_ip:
                potential_peers.add(partial_target_ip)
            # Ensure we don't try to remove rules targeting the main pod itself if somehow listed
            potential_peers.discard(target_pod_ip)
            ips_to_isolate_from = list(potential_peers)

    print(f"\nTarget Pod IP ('{target_pod_name}'): {target_pod_ip}")
    if action == "add-partial":
        print(
            f"Partially Isolating From Pod IP ('{partial_target_pod_name}'): {partial_target_ip or 'Not Found'}"
        )
    else:
        print(f"Other RabbitMQ Pod IPs: {other_pod_ips or 'None'}")
    print(f"Worker nodes involved: {list(all_nodes)}")

    # Determine iptables operation flag and descriptive verbs
    iptables_flag = (
        "-D" if action == "remove" else "-I"
    )  # Use -I (Insert) for add and add-partial
    action_verb = (
        "Removing"
        if action == "remove"
        else ("Adding partial" if action == "add-partial" else "Adding")
    )
    action_past = (
        "removed"
        if action == "remove"
        else ("partially applied" if action == "add-partial" else "applied")
    )

    print(f"\n--- {action_verb} iptables rules ---")

    if (
        not ips_to_isolate_from and action != "remove"
    ):  # Removal might proceed even if list is empty (e.g., only target pod exists)
        print(
            f"Skipping rule application as no target IPs were identified for action '{action}'."
        )
    elif not ips_to_isolate_from and action == "remove":
        print(
            "No specific peer IPs found to target for rule removal. Will check nodes anyway if any exist."
        )
        # Allow proceeding to node loop for removal, run_command_on_node handles non-existent rules gracefully.
    else:
        print(f"Targeting IPs for isolation rules: {ips_to_isolate_from}")

    overall_success = True
    if not all_nodes:
        print("No nodes to process.")
    else:
        for node in all_nodes:
            print(f"\nProcessing node: {node}")
            node_success = True

            if not ips_to_isolate_from and action == "remove":
                print(
                    "  No specific peer IPs to target for removal on this node, skipping specific rule commands."
                )
                # We could potentially add a command here to flush a custom chain if used, but sticking to specific rules for now.
                continue  # Skip to next node if removing and no peers were ever found

            if not ips_to_isolate_from and action != "remove":
                print(
                    f"  Skipping node {node} as there are no IPs to isolate from for action '{action}'."
                )
                continue

            for other_ip in ips_to_isolate_from:
                # Defensive check: Don't try to isolate a pod from itself.
                if other_ip == target_pod_ip:
                    print(f"  Skipping rule for {target_pod_ip} <-> {other_ip} (self)")
                    continue

                # Rule: Block traffic FROM target_pod_ip TO other_ip
                cmd1 = f"iptables {iptables_flag} FORWARD -s {target_pod_ip} -d {other_ip} -p tcp -j DROP"
                if not run_command_on_node(node, cmd1, action):
                    node_success = False

                # Rule: Block traffic FROM other_ip TO target_pod_ip
                cmd2 = f"iptables {iptables_flag} FORWARD -s {other_ip} -d {target_pod_ip} -p tcp -j DROP"
                if not run_command_on_node(node, cmd2, action):
                    node_success = False

            if not node_success:
                overall_success = False
                print(
                    f"Warning: Failed to {action} some rules on node '{node}'. Check logs above.",
                    file=sys.stderr,
                )

        if overall_success:
            print(
                f"\n--- iptables rules {action_past} successfully on all relevant nodes ---"
            )
        else:
            print(
                f"\n--- Warning: Some iptables rules failed to be {action_past}. Check logs above. ---",
                file=sys.stderr,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Add or remove iptables rules to isolate the {TARGET_POD_NAME} pod. "
        f"'add' isolates from all other rabbitmq-* pods. "
        f"'add-partial' isolates only from {PARTIAL_TARGET_POD_NAME}. "
        f"'remove' attempts to remove all isolation rules related to {TARGET_POD_NAME}."
    )
    parser.add_argument(
        "action",
        choices=["add", "remove", "add-partial"],
        help="Specify 'add' (full isolation), 'add-partial' (isolate from rabbitmq-1 only), or 'remove' (remove isolation).",
    )
    parser.add_argument(
        "--runner",
        default=COMMAND_RUNNER,
        help=f"Command runner to execute commands on nodes (e.g., docker, nerdctl). Default: {COMMAND_RUNNER}",
    )
    parser.add_argument(
        "--namespace",
        default=NAMESPACE,
        help=f"Kubernetes namespace for RabbitMQ pods. Default: {NAMESPACE}",
    )
    parser.add_argument(
        "--target-pod",
        default=TARGET_POD_NAME,
        help=f"Name of the RabbitMQ pod to isolate. Default: {TARGET_POD_NAME}",
    )
    parser.add_argument(
        "--partial-target-pod",
        default=PARTIAL_TARGET_POD_NAME,
        help=f"Name of the specific pod to isolate from in 'add-partial' mode. Default: {PARTIAL_TARGET_POD_NAME}",
    )
    parser.add_argument(
        "--label-selector",
        default=POD_LABEL_SELECTOR,
        help=f"Label selector to find RabbitMQ pods. Default: '{POD_LABEL_SELECTOR}'",
    )

    args = parser.parse_args()

    # Update global constants from args where necessary
    COMMAND_RUNNER = args.runner
    NAMESPACE = args.namespace
    TARGET_POD_NAME = args.target_pod
    PARTIAL_TARGET_POD_NAME = args.partial_target_pod
    POD_LABEL_SELECTOR = args.label_selector

    print("Configuration:")
    print(f"  Action: {args.action}")
    print(f"  Namespace: {NAMESPACE}")
    print(f"  Target Pod: {TARGET_POD_NAME}")
    if args.action == "add-partial":
        print(f"  Partial Isolation Peer: {PARTIAL_TARGET_POD_NAME}")
    print(f"  Pod Label Selector: {POD_LABEL_SELECTOR}")
    print(f"  Node Command Runner: {COMMAND_RUNNER}")

    manage_iptables_rules(
        v1,
        NAMESPACE,
        POD_LABEL_SELECTOR,
        TARGET_POD_NAME,
        PARTIAL_TARGET_POD_NAME,
        COMMAND_RUNNER,
        action=args.action,
    )

    print("\nScript finished.")
