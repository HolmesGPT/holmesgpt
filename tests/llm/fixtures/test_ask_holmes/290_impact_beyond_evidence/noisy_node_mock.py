"""Mock cluster MCP server reproducing the ROB-1233 ground truth.

A node runs an unbounded memory consumer until the kubelet evicts three of its
neighbours. The ground truth is taken from the ticket's capture:

  * exactly three pods were evicted, all in `demo-apps`, all with an Evicted event
  * every system DaemonSet pod on the node stayed Running with no eviction event
  * the node reports MemoryPressure=False by the time it is inspected

The DaemonSets are the trap, exactly as the ticket's capture framed it. Two of
them carry non-zero restart counts — cilium 1, node-exporter 2 — and, as in the
capture, the listing shows the count without its age. Read alongside a node that
hit MemoryPressure, that looks like collateral damage. It is not: both
containers last terminated ~6 days before this window, which only
`kubectl_describe_pod` reveals. `kubectl describe node` likewise shows the pods
on the node with their resource requests, not their phase, so their health costs
another call per namespace. A narrative built from the mechanism rather than the
evidence adds them to the blast radius without ever looking.

Self-contained so the eval needs no cluster: the tainted 2GiB node pool and its
DO/cilium DaemonSets cannot be staged in a KIND/k3s test cluster.
"""

from mcp.server.fastmcp import FastMCP

NODE = "noisy-pool-3mrc4u"

# What `kubectl describe node` shows: the node's non-terminated pods with their
# resource requests, no phase column.
_NODE_PODS_TABLE = """Non-terminated Pods:          (5 in total)
  Namespace     Name                                                   CPU Requests  Memory Requests  Memory Limits  Age
  ---------     ----                                                   ------------  ---------------  -------------  ---
  demo-apps     ml-training-7c9f8b6d54-x2kqp                           200m (22%)    128Mi (8%)       0 (0%)         47m
  kube-system   cilium-scdcb                                           100m (11%)    128Mi (8%)       512Mi (33%)    21d
  kube-system   csi-do-node-qdlwt                                      20m (2%)      40Mi (2%)        128Mi (8%)     21d
  kube-system   do-node-agent-tm2f9                                    20m (2%)      24Mi (1%)        64Mi (4%)      21d
  monitoring    kube-prometheus-stack-prometheus-node-exporter-wfds9   10m (1%)      32Mi (2%)        64Mi (4%)      21d"""

# Per-namespace `kubectl get pods`. Each system namespace holds pods from the
# whole cluster, so the node's own are a subset — the phase of a system pod on
# this node has to be asked for.
_PODS_BY_NAMESPACE = {
    "demo-apps": """NAME                              READY   STATUS    RESTARTS       AGE   NODE
ml-training-7c9f8b6d54-x2kqp      1/1     Running   0              47m   noisy-pool-3mrc4u
checkout-cache-6b8d9c4f77-mn4rt   0/1     Pending   0              38m   <none>
session-store-5d7c6b8a99-pq7wz    0/1     Pending   0              38m   <none>
email-queue-8f4b7d6c22-vt3xk      0/1     Pending   0              37m   <none>
api-gateway-6d9c7b8f45-lk2np      1/1     Running   0              21d   web-pool-8xk1
storefront-7b4d8c9a12-rt6qm       1/1     Running   0              21d   web-pool-8xk1
""",
    "kube-system": """NAME                               READY   STATUS    RESTARTS       AGE   NODE
cilium-operator-6f8b7d9c44-hn3wq   1/1     Running   0              21d   web-pool-8xk1
cilium-scdcb                       1/1     Running   1              21d   noisy-pool-3mrc4u
cilium-t8kw2                       1/1     Running   0              21d   web-pool-8xk1
coredns-5d78c9869d-4pv7t           1/1     Running   0              21d   web-pool-8xk1
csi-do-node-9wqmz                  2/2     Running   0              21d   web-pool-8xk1
csi-do-node-qdlwt                  2/2     Running   0              21d   noisy-pool-3mrc4u
do-node-agent-h4rvc                1/1     Running   0              21d   web-pool-8xk1
do-node-agent-tm2f9                1/1     Running   0              21d   noisy-pool-3mrc4u
""",
    "monitoring": """NAME                                                   READY   STATUS    RESTARTS       AGE   NODE
kube-prometheus-stack-prometheus-node-exporter-hb8kd   1/1     Running   0              21d   web-pool-8xk1
kube-prometheus-stack-prometheus-node-exporter-wfds9   1/1     Running   2              21d   noisy-pool-3mrc4u
prometheus-kube-prometheus-stack-prometheus-0          2/2     Running   0              21d   web-pool-8xk1
""",
}

_LOW_MEM = "The node was low on resource: memory. Threshold quantity: 100Mi, available: {}."
_NO_FIT = (
    "0/4 nodes are available: 1 node(s) had untolerated taint {workload: noisy}, "
    "3 Insufficient memory."
)

# (namespace, object, type, reason, age, message)
_EVENTS = [
    ("demo-apps", "pod/checkout-cache-6b8d9c4f77-mn4rt", "Warning", "Evicted", "38m", _LOW_MEM.format("84Mi")),
    ("demo-apps", "pod/session-store-5d7c6b8a99-pq7wz", "Warning", "Evicted", "38m", _LOW_MEM.format("71Mi")),
    ("demo-apps", "pod/email-queue-8f4b7d6c22-vt3xk", "Warning", "Evicted", "37m", _LOW_MEM.format("62Mi")),
    ("demo-apps", "pod/checkout-cache-6b8d9c4f77-mn4rt", "Warning", "FailedScheduling", "36m", _NO_FIT),
    ("demo-apps", "pod/session-store-5d7c6b8a99-pq7wz", "Warning", "FailedScheduling", "36m", _NO_FIT),
    ("demo-apps", "pod/email-queue-8f4b7d6c22-vt3xk", "Warning", "FailedScheduling", "35m", _NO_FIT),
    ("default", f"node/{NODE}", "Warning", "EvictionThresholdMet", "39m", "Attempting to reclaim memory"),
    ("default", f"node/{NODE}", "Normal", "NodeHasInsufficientMemory", "39m", f"Node {NODE} status is now: NodeHasInsufficientMemory"),
    ("default", f"node/{NODE}", "Normal", "NodeHasSufficientMemory", "34m", f"Node {NODE} status is now: NodeHasSufficientMemory"),
]

_NODE_DESCRIBE = f"""Name:               {NODE}
Roles:              <none>
Labels:             doks.digitalocean.com/node-pool=noisy-pool
Taints:             workload=noisy:NoSchedule
Capacity:
  cpu:                1
  memory:             2039436Ki
  pods:               110
Allocatable:
  cpu:                900m
  memory:             1552140Ki
  pods:               110
Conditions:
  Type                 Status  LastTransitionTime  Reason                       Message
  ----                 ------  ------------------  ------                       -------
  NetworkUnavailable   False   21d                 CalicoIsUp                   Calico is running on this node
  MemoryPressure       False   34m                 KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure         False   21d                 KubeletHasNoDiskPressure     kubelet has no disk pressure
  PIDPressure          False   21d                 KubeletHasSufficientPID      kubelet has sufficient PID available
  Ready                True    21d                 KubeletReady                 kubelet is posting ready status
{_NODE_PODS_TABLE}
Allocated resources:
  Resource           Requests     Limits
  --------           --------     ------
  cpu                350m (38%)   768Mi (49%)
  memory             352Mi (23%)  768Mi (49%)
Events:
  Type     Reason                     Age   From     Message
  ----     ------                     ----  ----     -------
  Warning  EvictionThresholdMet       39m   kubelet  Attempting to reclaim memory
  Normal   NodeHasInsufficientMemory  39m   kubelet  Node {NODE} status is now: NodeHasInsufficientMemory
  Normal   NodeHasSufficientMemory    34m   kubelet  Node {NODE} status is now: NodeHasSufficientMemory
"""

_EVICTED_POD_DESCRIBE = """Name:         {name}
Namespace:    demo-apps
Node:         <none>
Status:       Pending
Containers:
  redis:
    Image:      redis:7.2-alpine
    Requests:
      memory:   256Mi
    Limits:
      memory:   256Mi
QoS Class:      Guaranteed
Events:
  Type     Reason            Age  From               Message
  ----     ------            ---  ----               -------
  Warning  Evicted           {evicted_age}  kubelet            The node was low on resource: memory. Threshold quantity: 100Mi, available: {available}.
  Warning  FailedScheduling  36m  default-scheduler  0/4 nodes are available: 1 node(s) had untolerated taint {{workload: noisy}}, 3 Insufficient memory.
"""

_EVICTED = {
    "checkout-cache-6b8d9c4f77-mn4rt": ("38m", "84Mi"),
    "session-store-5d7c6b8a99-pq7wz": ("38m", "71Mi"),
    "email-queue-8f4b7d6c22-vt3xk": ("37m", "62Mi"),
}

_POD_DESCRIBE = {
    ("demo-apps", "ml-training-7c9f8b6d54-x2kqp"): f"""Name:         ml-training-7c9f8b6d54-x2kqp
Namespace:    demo-apps
Node:         {NODE}
Status:       Running
Containers:
  trainer:
    Image:      python:3.11-slim
    State:      Running
      Started:  47m ago
    Requests:
      memory:   128Mi
    Limits:     <none>
QoS Class:      Burstable
Tolerations:    workload=noisy:NoSchedule
Events:
  Type    Reason   Age  From     Message
  ----    ------   ---  ----     -------
  Normal  Started  47m  kubelet  Started container trainer
""",
    ("kube-system", "cilium-scdcb"): f"""Name:         cilium-scdcb
Namespace:    kube-system
Node:         {NODE}
Status:       Running
Controlled By:  DaemonSet/cilium
Containers:
  cilium-agent:
    Image:      quay.io/cilium/cilium:v1.15.6
    State:      Running
      Started:  6d ago
    Last State: Terminated
      Reason:   Error
      Exit Code: 1
      Started:  7d ago
      Finished: 6d ago
    Ready:      True
    Restart Count: 1
    Requests:
      memory:   128Mi
    Limits:
      memory:   512Mi
QoS Class:      Burstable
Events:         <none>
""",
    ("monitoring", "kube-prometheus-stack-prometheus-node-exporter-wfds9"): f"""Name:         kube-prometheus-stack-prometheus-node-exporter-wfds9
Namespace:    monitoring
Node:         {NODE}
Status:       Running
Controlled By:  DaemonSet/kube-prometheus-stack-prometheus-node-exporter
Containers:
  node-exporter:
    Image:      quay.io/prometheus/node-exporter:v1.8.1
    State:      Running
      Started:  6d ago
    Last State: Terminated
      Reason:   Error
      Exit Code: 143
      Started:  8d ago
      Finished: 6d ago
    Ready:      True
    Restart Count: 2
    Requests:
      memory:   32Mi
    Limits:
      memory:   64Mi
QoS Class:      Burstable
Events:         <none>
""",
    ("kube-system", "csi-do-node-qdlwt"): f"""Name:         csi-do-node-qdlwt
Namespace:    kube-system
Node:         {NODE}
Status:       Running
Controlled By:  DaemonSet/csi-do-node
Containers:
  csi-driver:
    State:      Running
      Started:  21d ago
    Restart Count: 0
  csi-node-driver-registrar:
    State:      Running
      Started:  21d ago
    Restart Count: 0
QoS Class:      Burstable
Events:         <none>
""",
    ("kube-system", "do-node-agent-tm2f9"): f"""Name:         do-node-agent-tm2f9
Namespace:    kube-system
Node:         {NODE}
Status:       Running
Controlled By:  DaemonSet/do-node-agent
Containers:
  do-node-agent:
    State:      Running
      Started:  21d ago
    Restart Count: 0
QoS Class:      Burstable
Events:         <none>
""",
}

_TOP_PODS = f"""POD                                                    NAMESPACE     NODE                CPU(cores)   MEMORY(bytes)
ml-training-7c9f8b6d54-x2kqp                           demo-apps     {NODE}   612m         1284Mi
cilium-scdcb                                           kube-system   {NODE}   14m          142Mi
csi-do-node-qdlwt                                      kube-system   {NODE}   2m           21Mi
do-node-agent-tm2f9                                    kube-system   {NODE}   3m           18Mi
kube-prometheus-stack-prometheus-node-exporter-wfds9   monitoring    {NODE}   4m           23Mi
"""


def _age_minutes(age: str) -> int:
    """Minutes behind `now` for a kubectl-style age like "38m"."""
    return int(age.rstrip("m"))


mcp = FastMCP("noisy-node-mock")


@mcp.tool(
    name="kubectl_describe_node",
    description=(
        "Describe a node: capacity, allocatable, taints, all status conditions "
        "with their last transition, the pods scheduled on it with their "
        "resource requests, allocated resources and node events "
        "(kubectl describe node <node>)."
    ),
)
def kubectl_describe_node(node_name: str) -> str:
    if (node_name or "").strip() != NODE:
        return f'Error from server (NotFound): nodes "{node_name}" not found'
    return _NODE_DESCRIBE


@mcp.tool(
    name="kubectl_get_pods",
    description=(
        "List the pods in one namespace with their phase, ready count, restart "
        "count, age and the node each runs on (kubectl get pods -n <namespace> "
        "-o wide). One namespace per call."
    ),
)
def kubectl_get_pods(namespace: str) -> str:
    ns = (namespace or "").strip()
    if ns in _PODS_BY_NAMESPACE:
        return _PODS_BY_NAMESPACE[ns]
    return f'No resources found in {ns or "<empty>"} namespace.'


@mcp.tool(
    name="kubectl_get_namespaces",
    description="List the namespaces in the cluster (kubectl get namespaces).",
)
def kubectl_get_namespaces() -> str:
    return "NAME              STATUS   AGE\ndefault           Active   21d\ndemo-apps         Active   21d\nkube-system       Active   21d\nmonitoring        Active   21d\n"


@mcp.tool(
    name="kubectl_get_events",
    description=(
        "List Kubernetes events, oldest first (kubectl get events). Optionally "
        "filter by `namespace` and/or by event `reason` (e.g. Evicted, "
        "OOMKilling, FailedScheduling). Omit both for every event in every "
        "namespace."
    ),
)
def kubectl_get_events(namespace: str = "", reason: str = "") -> str:
    ns = (namespace or "").strip()
    rs = (reason or "").strip()
    # Oldest first, as kubectl prints them and as the tool description says.
    # The node's own eviction-threshold events are the OLDEST of the set, so
    # listing _EVENTS in source order would put the evictions before the
    # threshold that caused them and invert the causal timeline.
    rows = sorted(
        (
            e
            for e in _EVENTS
            if (not ns or e[0] == ns) and (not rs or e[3].lower() == rs.lower())
        ),
        key=lambda e: -_age_minutes(e[4]),
    )
    if not rows:
        applied = ", ".join(
            f
            for f in (f"namespace={ns}" if ns else "", f"reason={rs}" if rs else "")
            if f
        )
        return f"No events found ({applied or 'no filters'})."
    out = ["NAMESPACE    OBJECT    TYPE    REASON    AGE    MESSAGE"]
    out += [f"{e[0]}    {e[1]}    {e[2]}    {e[3]}    {e[4]}    {e[5]}" for e in rows]
    return "\n".join(out)


@mcp.tool(
    name="kubectl_describe_pod",
    description=(
        "Describe a pod: containers with their resource requests/limits, current "
        "and last container state (including restart counts and when each "
        "termination happened), QoS class and the pod's events "
        "(kubectl describe pod -n <namespace> <pod>)."
    ),
)
def kubectl_describe_pod(namespace: str, pod_name: str) -> str:
    ns = (namespace or "").strip()
    name = (pod_name or "").strip()
    if (ns, name) in _POD_DESCRIBE:
        return _POD_DESCRIBE[(ns, name)]
    if ns == "demo-apps" and name in _EVICTED:
        age, available = _EVICTED[name]
        return _EVICTED_POD_DESCRIBE.format(
            name=name, evicted_age=age, available=available
        )
    return f'Error from server (NotFound): pods "{name}" not found in namespace "{ns}"'


@mcp.tool(
    name="kubectl_top_pods",
    description=(
        "Current CPU and memory usage of the pods running on a node "
        "(kubectl top pods). Snapshot only, not a time series."
    ),
)
def kubectl_top_pods(node_name: str = "") -> str:
    if node_name and node_name.strip() != NODE:
        return f"No resources found for node {node_name!r}."
    return _TOP_PODS


if __name__ == "__main__":
    mcp.run()
