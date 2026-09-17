"""Mock cluster MCP server reproducing the ROB-1233 ground truth.

The scenario is robusta-dev/triage-demo `scenarios/noisy-neighbor.yaml` on the
tainted 1-node `noisy-pool` (s-1vcpu-2gb): an `ml-training` Job requests most
of the node and stays just under its request while three single-replica
services exceed their small requests slightly, so when memory runs out the
kubelet evicts the three services and the Job survives. Ground truth from the
ticket's capture:

  * exactly three pods were evicted, all in `demo-apps`, all with an Evicted event
  * every system pod on the node is Running and none has an eviction event
  * the node reports MemoryPressure=False by the time it is inspected

The trap is the one the ticket was filed for — a narrative that adds "system
DaemonSets also affected" — given the strongest honest bait. The node's
node-exporter pod is 37 minutes old, the same minute the evictions happened,
because the monitoring chart was rolled out during the window: the
daemonset-controller deleted the old pod and created this one, on this node
AND on web-pool, with a new image tag. That is in the events, in the pod's
describe (`pod-template-generation: 2`, Restart Count 0, no Evicted) and in
the sibling pod's identical age. Reading the timeline as "node-exporter was
evicted / restarted by the pressure" is the failure; reading the reason first
is the fix. cilium keeps a 1-restart count whose termination is six days old.

Self-contained so the eval needs no cluster: the DO node pool and its
cilium/CSI DaemonSets cannot be staged in a KIND/k3s test cluster.
"""

from mcp.server.fastmcp import FastMCP

NODE = "noisy-pool-3mrc4u"
NX_POD = "kube-prometheus-stack-prometheus-node-exporter-wfds9"
NX_POD_OLD = "kube-prometheus-stack-prometheus-node-exporter-p2x7d"
NX_POD_WEB = "kube-prometheus-stack-prometheus-node-exporter-hb8kd"
NX_POD_WEB_OLD = "kube-prometheus-stack-prometheus-node-exporter-6kd9s"
NX_DS = "kube-prometheus-stack-prometheus-node-exporter"

# What `kubectl describe node` shows: the node's non-terminated pods with their
# resource requests, no phase column.
_NODE_PODS_TABLE = f"""Non-terminated Pods:          (5 in total)
  Namespace     Name                                                   CPU Requests  Memory Requests  Memory Limits  Age
  ---------     ----                                                   ------------  ---------------  -------------  ---
  demo-apps     ml-training-7c9f8b6d54-x2kqp                           200m (22%)    1280Mi (84%)     0 (0%)         47m
  kube-system   cilium-scdcb                                           100m (11%)    128Mi (8%)       512Mi (33%)    21d
  kube-system   csi-do-node-qdlwt                                      20m (2%)      40Mi (2%)        128Mi (8%)     21d
  kube-system   do-node-agent-tm2f9                                    20m (2%)      24Mi (1%)        64Mi (4%)      21d
  monitoring    {NX_POD}   10m (1%)      32Mi (2%)        64Mi (4%)      37m"""

# Per-namespace `kubectl get pods`. Each system namespace holds pods from the
# whole cluster, so the node's own are a subset.
_PODS_BY_NAMESPACE = {
    "demo-apps": f"""NAME                              READY   STATUS    RESTARTS   AGE   NODE
ml-training-7c9f8b6d54-x2kqp      1/1     Running   0          47m   {NODE}
checkout-cache-6b8d9c4f77-mn4rt   0/1     Pending   0          38m   <none>
session-store-5d7c6b8a99-pq7wz    0/1     Pending   0          38m   <none>
email-queue-8f4b7d6c22-vt3xk      0/1     Pending   0          37m   <none>
api-gateway-6d9c7b8f45-lk2np      1/1     Running   0          21d   web-pool-8xk1
storefront-7b4d8c9a12-rt6qm       1/1     Running   0          21d   web-pool-8xk1
""",
    "kube-system": f"""NAME                               READY   STATUS    RESTARTS   AGE   NODE
cilium-operator-6f8b7d9c44-hn3wq   1/1     Running   0          21d   web-pool-8xk1
cilium-scdcb                       1/1     Running   1          21d   {NODE}
cilium-t8kw2                       1/1     Running   0          21d   web-pool-8xk1
coredns-5d78c9869d-4pv7t           1/1     Running   0          21d   web-pool-8xk1
csi-do-node-9wqmz                  2/2     Running   0          21d   web-pool-8xk1
csi-do-node-qdlwt                  2/2     Running   0          21d   {NODE}
do-node-agent-h4rvc                1/1     Running   0          21d   web-pool-8xk1
do-node-agent-tm2f9                1/1     Running   0          21d   {NODE}
""",
    "monitoring": f"""NAME                                                   READY   STATUS    RESTARTS   AGE   NODE
{NX_POD_WEB}   1/1     Running   0          37m   web-pool-8xk1
{NX_POD}   1/1     Running   0          37m   {NODE}
prometheus-kube-prometheus-stack-prometheus-0          2/2     Running   0          21d   web-pool-8xk1
""",
}

_LOW_MEM = (
    "The node was low on resource: memory. Threshold quantity: 100Mi, available: {avail}. "
    "Container {ctr} was using {used}, request is 64Mi, has larger consumption of memory."
)
_NO_FIT = (
    "0/4 nodes are available: 1 Insufficient memory, "
    "3 node(s) didn't match Pod's node affinity/selector. "
    "preemption: 0/4 nodes are available: 1 No preemption victims found for incoming pod, "
    "3 Preemption is not helpful for scheduling."
)

# (namespace, object, type, reason, age, from, message)
_EVENTS = [
    ("default", f"node/{NODE}", "Warning", "EvictionThresholdMet", "39m", "kubelet", "Attempting to reclaim memory"),
    ("default", f"node/{NODE}", "Normal", "NodeHasInsufficientMemory", "39m", "kubelet", f"Node {NODE} status is now: NodeHasInsufficientMemory"),
    ("monitoring", f"daemonset/{NX_DS}", "Normal", "SuccessfulDelete", "38m", "daemonset-controller", f"Deleted pod: {NX_POD_OLD}"),
    ("demo-apps", "pod/checkout-cache-6b8d9c4f77-mn4rt", "Warning", "Evicted", "38m", "kubelet", _LOW_MEM.format(avail="84Mi", ctr="checkout-cache", used="98Mi")),
    ("demo-apps", "pod/session-store-5d7c6b8a99-pq7wz", "Warning", "Evicted", "38m", "kubelet", _LOW_MEM.format(avail="71Mi", ctr="session-store", used="97Mi")),
    ("monitoring", f"daemonset/{NX_DS}", "Normal", "SuccessfulCreate", "37m", "daemonset-controller", f"Created pod: {NX_POD}"),
    ("monitoring", f"pod/{NX_POD}", "Normal", "Scheduled", "37m", "default-scheduler", f"Successfully assigned monitoring/{NX_POD} to {NODE}"),
    ("monitoring", f"pod/{NX_POD}", "Normal", "Pulled", "37m", "kubelet", 'Container image "quay.io/prometheus/node-exporter:v1.9.1" already present on machine'),
    ("monitoring", f"pod/{NX_POD}", "Normal", "Started", "37m", "kubelet", "Started container node-exporter"),
    ("monitoring", f"pod/{NX_POD}", "Warning", "Unhealthy", "37m", "kubelet", 'Readiness probe failed: Get "http://10.244.3.17:9100/": dial tcp 10.244.3.17:9100: connect: connection refused'),
    ("demo-apps", "pod/email-queue-8f4b7d6c22-vt3xk", "Warning", "Evicted", "37m", "kubelet", _LOW_MEM.format(avail="62Mi", ctr="email-queue", used="99Mi")),
    ("monitoring", f"daemonset/{NX_DS}", "Normal", "SuccessfulDelete", "37m", "daemonset-controller", f"Deleted pod: {NX_POD_WEB_OLD}"),
    ("monitoring", f"daemonset/{NX_DS}", "Normal", "SuccessfulCreate", "36m", "daemonset-controller", f"Created pod: {NX_POD_WEB}"),
    ("monitoring", f"pod/{NX_POD_WEB}", "Normal", "Started", "36m", "kubelet", "Started container node-exporter"),
    ("demo-apps", "pod/checkout-cache-6b8d9c4f77-mn4rt", "Warning", "FailedScheduling", "36m", "default-scheduler", _NO_FIT),
    ("demo-apps", "pod/session-store-5d7c6b8a99-pq7wz", "Warning", "FailedScheduling", "36m", "default-scheduler", _NO_FIT),
    ("demo-apps", "pod/email-queue-8f4b7d6c22-vt3xk", "Warning", "FailedScheduling", "35m", "default-scheduler", _NO_FIT),
    ("default", f"node/{NODE}", "Normal", "NodeHasSufficientMemory", "34m", "kubelet", f"Node {NODE} status is now: NodeHasSufficientMemory"),
]

_NODE_DESCRIBE = f"""Name:               {NODE}
Roles:              <none>
Labels:             doks.digitalocean.com/node-pool=noisy-pool
                    node.kubernetes.io/instance-type=s-1vcpu-2gb
Taints:             demo=noisy:NoSchedule
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
  NetworkUnavailable   False   21d                 CiliumIsUp                   Cilium is running on this node
  MemoryPressure       False   34m                 KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure         False   21d                 KubeletHasNoDiskPressure     kubelet has no disk pressure
  PIDPressure          False   21d                 KubeletHasSufficientPID      kubelet has sufficient PID available
  Ready                True    21d                 KubeletReady                 kubelet is posting ready status
{_NODE_PODS_TABLE}
Allocated resources:
  Resource           Requests      Limits
  --------           --------      ------
  cpu                350m (38%)    0 (0%)
  memory             1504Mi (99%)  768Mi (50%)
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
Controlled By:  ReplicaSet/{rs}
Node-Selectors:  doks.digitalocean.com/node-pool=noisy-pool
Tolerations:     demo=noisy:NoSchedule
Containers:
  {ctr}:
    Image:      busybox:1.37
    Requests:
      cpu:      5m
      memory:   64Mi
    Limits:     <none>
QoS Class:      Burstable
Events:
  Type     Reason            Age  From               Message
  ----     ------            ---  ----               -------
  Warning  Evicted           {evicted_age}  kubelet            {evicted_msg}
  Warning  FailedScheduling  {sched_age}  default-scheduler  {no_fit}
"""

# name -> (evicted age, avail, used, FailedScheduling age)
_EVICTED = {
    "checkout-cache-6b8d9c4f77-mn4rt": ("38m", "84Mi", "98Mi", "36m"),
    "session-store-5d7c6b8a99-pq7wz": ("38m", "71Mi", "97Mi", "36m"),
    "email-queue-8f4b7d6c22-vt3xk": ("37m", "62Mi", "99Mi", "35m"),
}

_POD_DESCRIBE = {
    ("demo-apps", "ml-training-7c9f8b6d54-x2kqp"): f"""Name:         ml-training-7c9f8b6d54-x2kqp
Namespace:    demo-apps
Node:         {NODE}
Status:       Running
Controlled By:  Job/ml-training
Node-Selectors:  doks.digitalocean.com/node-pool=noisy-pool
Tolerations:     demo=noisy:NoSchedule
Containers:
  ml-training:
    Image:      busybox:1.37
    State:      Running
      Started:  47m ago
    Restart Count: 0
    Requests:
      cpu:      200m
      memory:   1280Mi
    Limits:     <none>
    Mounts:
      /cache from cache (rw)
Volumes:
  cache:
    Type:       EmptyDir (a temporary directory that shares a pod's lifetime)
    Medium:     Memory
QoS Class:      Burstable
Events:
  Type    Reason   Age  From     Message
  ----    ------   ---  ----     -------
  Normal  Started  47m  kubelet  Started container ml-training
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
    ("monitoring", NX_POD): f"""Name:         {NX_POD}
Namespace:    monitoring
Node:         {NODE}
Status:       Running
Annotations:  pod-template-generation: 2
Controlled By:  DaemonSet/{NX_DS}
Containers:
  node-exporter:
    Image:      quay.io/prometheus/node-exporter:v1.9.1
    State:      Running
      Started:  37m ago
    Ready:      True
    Restart Count: 0
    Requests:
      memory:   32Mi
    Limits:
      memory:   64Mi
QoS Class:      Burstable
Tolerations:    op=Exists
Events:
  Type    Reason     Age  From               Message
  ----    ------     ---  ----               -------
  Normal  Scheduled  37m  default-scheduler  Successfully assigned monitoring/{NX_POD} to {NODE}
  Normal  Pulled     37m  kubelet            Container image "quay.io/prometheus/node-exporter:v1.9.1" already present on machine
  Normal  Created    37m  kubelet            Created container node-exporter
  Normal  Started    37m  kubelet            Started container node-exporter
  Warning  Unhealthy  37m (x1 over 37m)  kubelet  Readiness probe failed: Get "http://10.244.3.17:9100/": dial tcp 10.244.3.17:9100: connect: connection refused
""",
    ("monitoring", NX_POD_WEB): f"""Name:         {NX_POD_WEB}
Namespace:    monitoring
Node:         web-pool-8xk1
Status:       Running
Annotations:  pod-template-generation: 2
Controlled By:  DaemonSet/{NX_DS}
Containers:
  node-exporter:
    Image:      quay.io/prometheus/node-exporter:v1.9.1
    State:      Running
      Started:  36m ago
    Ready:      True
    Restart Count: 0
QoS Class:      Burstable
Events:
  Type    Reason     Age  From               Message
  ----    ------     ---  ----               -------
  Normal  Scheduled  36m  default-scheduler  Successfully assigned monitoring/{NX_POD_WEB} to web-pool-8xk1
  Normal  Started    36m  kubelet            Started container node-exporter
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
{NX_POD}   monitoring    {NODE}   4m           23Mi
"""


def _age_minutes(age: str) -> int:
    """Minutes behind `now` for a kubectl-style age like "38m"."""
    return int(age.rstrip("m"))


mcp = FastMCP("noisy-node-mock")


@mcp.tool(
    name="kubectl_describe_node",
    description=(
        "Describe a node: capacity, allocatable, taints, all status conditions "
        "with their last transition, the non-terminated pods scheduled on it "
        "with their resource requests, and the node's recent events "
        "(kubectl describe node <name>)."
    ),
)
def kubectl_describe_node(node_name: str) -> str:
    if (node_name or "").strip() != NODE:
        return f'Error from server (NotFound): nodes "{node_name}" not found'
    return _NODE_DESCRIBE


def _all_pods_rows():
    for ns, table in _PODS_BY_NAMESPACE.items():
        for line in table.splitlines()[1:]:
            if line.strip():
                yield ns, line


@mcp.tool(
    name="kubectl_get_pods",
    description=(
        "List pods with their phase, ready count, restart count, age and node "
        "(kubectl get pods -o wide). Give a namespace to list one namespace, or "
        "leave it empty for all namespaces (-A). Optionally restrict to the pods "
        "scheduled on one node (--field-selector spec.nodeName=<node>)."
    ),
)
def kubectl_get_pods(namespace: str = "", node_name: str = "") -> str:
    ns = (namespace or "").strip()
    node = (node_name or "").strip()
    if ns in ("-A", "--all-namespaces", "all"):
        ns = ""
    if ns and ns not in _PODS_BY_NAMESPACE:
        return f"No resources found in {ns} namespace."
    rows = [
        (r_ns, line)
        for r_ns, line in _all_pods_rows()
        if (not ns or r_ns == ns) and (not node or line.split()[-1] == node)
    ]
    if not rows:
        return f"No resources found (namespace={ns or 'all'}, node={node or 'any'})."
    header = f"{'NAMESPACE':<13}NAME                                                   READY   STATUS    RESTARTS   AGE   NODE"
    return header + "\n" + "\n".join(f"{r_ns:<13}{line}" for r_ns, line in rows) + "\n"


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
        "filter to one namespace and/or one event reason (e.g. Evicted, "
        "FailedScheduling). Leave both empty for every namespace."
    ),
)
def kubectl_get_events(namespace: str = "", reason: str = "") -> str:
    ns = (namespace or "").strip()
    rs = (reason or "").strip()
    rows = [
        e
        for e in _EVENTS
        if (not ns or e[0] == ns) and (not rs or e[3].lower() == rs.lower())
    ]
    if not rows:
        return f"No events found (namespace={ns or 'all'}, reason={rs or 'any'})."
    # Oldest first, as kubectl prints them and as the tool description says.
    rows.sort(key=lambda e: -_age_minutes(e[4]))
    header = f"{'NAMESPACE':<12}{'LAST SEEN':<11}{'TYPE':<9}{'REASON':<27}{'OBJECT':<58}{'FROM':<22}MESSAGE"
    lines = [header]
    for e_ns, obj, typ, rsn, age, src, msg in rows:
        lines.append(f"{e_ns:<12}{age:<11}{typ:<9}{rsn:<27}{obj:<58}{src:<22}{msg}")
    return "\n".join(lines) + "\n"


@mcp.tool(
    name="kubectl_describe_pod",
    description=(
        "Describe a pod: controller, containers with their image, resource "
        "requests/limits, current and last state (with termination reason and "
        "exit code), restart count, and the pod's events "
        "(kubectl describe pod -n <namespace> <name>)."
    ),
)
def kubectl_describe_pod(namespace: str, pod_name: str) -> str:
    ns = (namespace or "").strip()
    name = (pod_name or "").strip()
    if (ns, name) in _POD_DESCRIBE:
        return _POD_DESCRIBE[(ns, name)]
    if ns == "demo-apps" and name in _EVICTED:
        evicted_age, avail, used, sched_age = _EVICTED[name]
        ctr = name.rsplit("-", 2)[0]
        return _EVICTED_POD_DESCRIBE.format(
            name=name,
            rs=name.rsplit("-", 1)[0],
            ctr=ctr,
            evicted_age=evicted_age,
            evicted_msg=_LOW_MEM.format(avail=avail, ctr=ctr, used=used),
            sched_age=sched_age,
            no_fit=_NO_FIT,
        )
    return f'Error from server (NotFound): pods "{name}" not found in namespace "{ns}"'


@mcp.tool(
    name="kubectl_top_pods",
    description=(
        "Current CPU and memory usage of the pods running on a node "
        "(kubectl top pods -A --field-selector spec.nodeName=<node>). "
        "Pods that are not running on the node have no row."
    ),
)
def kubectl_top_pods(node_name: str = "") -> str:
    if node_name and node_name.strip() != NODE:
        return f"No resources found for node {node_name!r}."
    return _TOP_PODS


if __name__ == "__main__":
    mcp.run()
