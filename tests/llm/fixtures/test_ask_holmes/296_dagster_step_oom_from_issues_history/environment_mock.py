"""Mock data sources for a Dagster step whose pod was OOMKilled and deleted.

Ground truth: run a3f9c2e1 of `daily_metrics_job` failed because the step pod
`dagster-step-4b9e...-x7k2p` was OOMKilled at 11:50:16 UTC on 2026-07-15. The
Robusta platform recorded a PodOOMKilled finding for it (issues_metadata.json).

Every other source an investigation would reach first is silent about the OOM,
the way it is in production:

  * the pod is gone (the step job's pod was deleted 2s after the kill), so
    there is no container status to read, and kubelet emits no event for a
    container OOM kill
  * the step pod's last log line is an innocuous warning 3s before the kill
  * Prometheus scrapes every 30s; the last sample before the kill is 1.86 GiB
    of the 8 GiB limit, so the series plateaus well under the limit
  * kube-state-metrics never saw the terminated container
  * the node ships only aws-node (CNI) logs to Loki, no kernel log
  * Dagster reports only that the step's Kubernetes job failed

Reading "memory peaked far below the limit and no OOM in the logs" as "not an
OOM" is the failure; the PodOOMKilled finding in the alert history is the fix.

Self-contained so the eval needs no cluster: a Dagster+ deployment, Loki and a
managed Prometheus cannot be staged in a test cluster.
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

NOW = datetime(2026, 7, 15, 12, 5, 0, tzinfo=timezone.utc)
NS = "dagster"
NODE = "ip-10-0-37-142.eu-west-1.compute.internal"
JOB = "daily_metrics_job"
STEP = "warehouse__events__sessionized_events"
DOWNSTREAM = "daily_metrics_report"
IMAGE = "registry.example.com/data/warehouse:2026-07-14_214930"
GIB = 1024**3


def _t(hms: str, day: int = 15) -> datetime:
    h, m, s = hms.split(":")
    sec = float(s)
    return datetime(
        2026,
        7,
        day,
        int(h),
        int(m),
        int(sec),
        int(round((sec % 1) * 1e6)),
        tzinfo=timezone.utc,
    )


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _age(ts: datetime) -> str:
    seconds = int((NOW - ts).total_seconds())
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d"


class Run:
    def __init__(
        self, run_id, start, end, status, step_hash, pod_suffix, worker_suffix, day=15
    ):
        self.run_id = run_id
        self.start = _t(start, day)
        self.end = _t(end, day)
        self.status = status
        self.step_job = f"dagster-step-{step_hash}"
        self.step_pod = f"{self.step_job}-{pod_suffix}"
        self.run_job = f"dagster-run-{run_id}"
        self.run_pod = f"{self.run_job}-{worker_suffix}"
        offset = self.start - _t("11:48:04")
        self.step_launched = _t("11:48:31") + offset
        self.container_started = _t("11:49:38") + offset
        self.killed = _t("11:50:16") + offset
        self.job_failed = _t("11:50:19") + offset


# Today's four scheduled runs all failed the same way, starting with the first
# run after the code location was redeployed at 21:57 the night before.
RUNS = [
    Run(
        "5e0b8d21-3c47-4f9a-8e12-6a7b9c0d1e2f",
        "05:48:02",
        "05:50:34",
        "FAILURE",
        "1f6a3c9e7b2d4a8c5e0f9b3d7a1c6e4b",
        "h2t6v",
        "k8d2p",
    ),
    Run(
        "7c4e1a90-2b58-4d3f-9a6e-1f2b3c4d5e6f",
        "07:48:05",
        "07:50:37",
        "FAILURE",
        "8e2b5d1a9c7f3e6b0a4d8c2f6e1b9a7d",
        "b9w3r",
        "z5f7c",
    ),
    Run(
        "c81f2d6b-9e04-4a7c-b3d5-2e6f7a8b9c0d",
        "09:48:01",
        "09:50:33",
        "FAILURE",
        "9d2c7a1e5f3b8c4d6e0a2f7b1c9d5e3a",
        "m4n8q",
        "r2v6j",
    ),
    Run(
        "a3f9c2e1-7b4d-4e8a-9c21-5d6e7f8a9b0c",
        "11:48:04",
        "11:50:36",
        "FAILURE",
        "4b9e1f7c2a8d6e3f5c1b9a7d4e2f8c6a",
        "x7k2p",
        "qwz8m",
    ),
]
TARGET = RUNS[-1]
YESTERDAY_RUNS = [
    (
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"run-{h}")),
        _t(f"{h:02d}:48:03", 14),
        _t(f"{h:02d}:56:41", 14),
    )
    for h in range(5, 22, 2)
]

mcp = FastMCP("observability")


# --------------------------------------------------------------------------
# Kubernetes
# --------------------------------------------------------------------------

_KIND_ALIASES = {
    "po": "pod",
    "pod": "pod",
    "pods": "pod",
    "job": "job",
    "jobs": "job",
    "jobs.batch": "job",
    "node": "node",
    "nodes": "node",
    "no": "node",
    "deploy": "deployment",
    "deployment": "deployment",
    "deployments": "deployment",
    "event": "event",
    "events": "event",
    "ev": "event",
}


def _kind(kind: str) -> str:
    return _KIND_ALIASES.get(kind.strip().lower(), kind.strip().lower())


def _not_found(kind: str, name: str) -> str:
    plural = {
        "pod": "pods",
        "job": "jobs.batch",
        "node": "nodes",
        "deployment": "deployments.apps",
    }.get(kind, kind)
    return f'Error from server (NotFound): {plural} "{name}" not found'


AGENT_POD = "dagster-cloud-agent-6f8d9c7b5-2kq9x"
CODE_POD = "dagster-code-warehouse-7d5f8b9c4-p8r2t"
LIVE_PODS = {
    AGENT_POD: ("1/1", "Running", 0, _t("08:12:44", 6), NODE),
    CODE_POD: (
        "1/1",
        "Running",
        0,
        _t("21:57:12", 14),
        "ip-10-0-51-9.eu-west-1.compute.internal",
    ),
}


def _get_pods() -> str:
    lines = [
        "NAME                                     READY   STATUS    RESTARTS   AGE     IP            NODE"
    ]
    for name, (ready, status, restarts, created, node) in LIVE_PODS.items():
        lines.append(
            f"{name:<40} {ready:<7} {status:<9} {restarts:<10} {_age(created):<7} 10.0.37.{len(name)}   {node}"
        )
    return "\n".join(lines)


def _get_jobs() -> str:
    lines = [
        "NAME                                                         STATUS     COMPLETIONS   DURATION   AGE"
    ]
    for run in RUNS:
        lines.append(
            f"{run.run_job:<60} Complete   1/1           {int((run.end - run.start).total_seconds())}s       {_age(run.start)}"
        )
        lines.append(
            f"{run.step_job:<60} Failed     0/1           {int((run.job_failed - run.step_launched).total_seconds())}s       {_age(run.step_launched)}"
        )
    return "\n".join(lines)


def _job_describe(run: Run) -> str:
    return f"""Name:             {run.step_job}
Namespace:        {NS}
Selector:         batch.kubernetes.io/controller-uid=5f1c0a9e-{run.step_job[13:17]}-4e2b-8c1d-0b9a8c7d6e5f
Labels:           app.kubernetes.io/component=step_worker
                  app.kubernetes.io/instance=dagster
                  app.kubernetes.io/name=dagster
                  app.kubernetes.io/part-of=dagster
                  dagster/job={JOB}
                  dagster/op={STEP}
                  dagster/run-id={run.run_id}
Annotations:      <none>
Parallelism:      1
Completions:      1
Completion Mode:  NonIndexed
Suspend:          false
Backoff Limit:    0
TTL Seconds After Finished:  86400
Start Time:       {run.step_launched.strftime('%a, %d %b %Y %H:%M:%S +0000')}
Pods Statuses:    0 Active (0 Ready) / 0 Succeeded / 1 Failed
Pod Template:
  Labels:  app.kubernetes.io/component=step_worker
           dagster/job={JOB}
           dagster/op={STEP}
           dagster/run-id={run.run_id}
  Service Account:  dagster-cloud-agent
  Containers:
   dagster:
    Image:      {IMAGE}
    Port:       <none>
    Host Port:  <none>
    Args:
      dagster
      api
      execute_step
    Limits:
      cpu:     4
      memory:  8Gi
    Requests:
      cpu:     2
      memory:  4Gi
    Environment:
      DAGSTER_RUN_JOB_NAME:  {JOB}
      SPARK_DRIVER_MEMORY:   6g
    Mounts:                  <none>
  Volumes:                   <none>
  Node-Selectors:            <none>
  Tolerations:               <none>
Conditions:
  Type           Status  Reason
  ----           ------  ------
  FailureTarget  True    BackoffLimitExceeded
  Failed         True    BackoffLimitExceeded
Events:
{_events_table(_job_events(run), header=True, indent="  ")}"""


def _job_yaml(run: Run) -> str:
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  creationTimestamp: "{run.step_launched.strftime('%Y-%m-%dT%H:%M:%SZ')}"
  labels:
    app.kubernetes.io/component: step_worker
    dagster/job: {JOB}
    dagster/op: {STEP}
    dagster/run-id: {run.run_id}
  name: {run.step_job}
  namespace: {NS}
spec:
  backoffLimit: 0
  completions: 1
  parallelism: 1
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      containers:
      - args: ["dagster", "api", "execute_step"]
        env:
        - name: DAGSTER_RUN_JOB_NAME
          value: {JOB}
        - name: SPARK_DRIVER_MEMORY
          value: 6g
        image: {IMAGE}
        name: dagster
        resources:
          limits:
            cpu: "4"
            memory: 8Gi
          requests:
            cpu: "2"
            memory: 4Gi
      restartPolicy: Never
      serviceAccountName: dagster-cloud-agent
status:
  conditions:
  - lastProbeTime: "{run.job_failed.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    lastTransitionTime: "{run.job_failed.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    message: Job has reached the specified backoff limit
    reason: BackoffLimitExceeded
    status: "True"
    type: FailureTarget
  - lastProbeTime: "{run.job_failed.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    lastTransitionTime: "{run.job_failed.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    message: Job has reached the specified backoff limit
    reason: BackoffLimitExceeded
    status: "True"
    type: Failed
  failed: 1
  ready: 0
  startTime: "{run.step_launched.strftime('%Y-%m-%dT%H:%M:%SZ')}"
  terminating: 0
  uncountedTerminatedPods: {{}}"""


# (time, type, reason, object, message). Kubernetes keeps events for one hour,
# so only the 11:48 run's are still there. The kubelet emits no event when a
# container is OOM killed.
def _job_events(run: Run) -> List[Tuple[datetime, str, str, str, str]]:
    if NOW - run.step_launched > timedelta(hours=1):
        return []
    return [
        (
            run.step_launched,
            "Normal",
            "SuccessfulCreate",
            f"job/{run.step_job}",
            f"Created pod: {run.step_pod}",
        ),
        (
            run.job_failed,
            "Warning",
            "BackoffLimitExceeded",
            f"job/{run.step_job}",
            "Job has reached the specified backoff limit",
        ),
    ]


def _pod_events(run: Run) -> List[Tuple[datetime, str, str, str, str]]:
    if NOW - run.step_launched > timedelta(hours=1):
        return []
    pod = f"pod/{run.step_pod}"
    return [
        (
            run.step_launched,
            "Normal",
            "Scheduled",
            pod,
            f"Successfully assigned {NS}/{run.step_pod} to {NODE}",
        ),
        (
            run.step_launched + timedelta(seconds=1),
            "Normal",
            "Pulling",
            pod,
            f'Pulling image "{IMAGE}"',
        ),
        (
            run.container_started - timedelta(seconds=2),
            "Normal",
            "Pulled",
            pod,
            f'Successfully pulled image "{IMAGE}" in 1m4.112s (1m4.112s including waiting). Image size: 2873310452 bytes.',
        ),
        (
            run.container_started - timedelta(seconds=1),
            "Normal",
            "Created",
            pod,
            "Created container: dagster",
        ),
        (run.container_started, "Normal", "Started", pod, "Started container dagster"),
    ]


def _run_pod_events(run: Run) -> List[Tuple[datetime, str, str, str, str]]:
    if NOW - run.start > timedelta(hours=1):
        return []
    pod = f"pod/{run.run_pod}"
    return [
        (
            run.start,
            "Normal",
            "Scheduled",
            pod,
            f"Successfully assigned {NS}/{run.run_pod} to {NODE}",
        ),
        (
            run.start + timedelta(seconds=2),
            "Normal",
            "Pulled",
            pod,
            f'Container image "{IMAGE}" already present on machine',
        ),
        (
            run.start + timedelta(seconds=2),
            "Normal",
            "Created",
            pod,
            "Created container: dagster",
        ),
        (
            run.start + timedelta(seconds=3),
            "Normal",
            "Started",
            pod,
            "Started container dagster",
        ),
        (
            run.end + timedelta(seconds=2),
            "Normal",
            "Completed",
            f"job/{run.run_job}",
            "Job completed",
        ),
    ]


def _all_events() -> List[Tuple[datetime, str, str, str, str]]:
    events = []
    for run in RUNS:
        events += _job_events(run) + _pod_events(run) + _run_pod_events(run)
    return sorted(events)


def _events_table(events, header: bool = True, indent: str = "") -> str:
    if not events:
        return (
            f"{indent}<none>" if indent else "No resources found in dagster namespace."
        )
    rows = []
    if header:
        rows.append(
            f"{indent}LAST SEEN   TYPE      REASON                 OBJECT                                                        MESSAGE"
        )
    for ts, typ, reason, obj, message in events:
        rows.append(f"{indent}{_age(ts):<11} {typ:<9} {reason:<22} {obj:<61} {message}")
    return "\n".join(rows)


def _node_describe(name: str) -> str:
    return f"""Name:               {name}
Roles:              <none>
Labels:             eks.amazonaws.com/nodegroup=data-workers
                    node.kubernetes.io/instance-type=r6i.2xlarge
                    topology.kubernetes.io/zone=eu-west-1a
Taints:             <none>
Unschedulable:      false
Conditions:
  Type             Status  LastHeartbeatTime                 Reason                       Message
  ----             ------  -----------------                 ------                       -------
  MemoryPressure   False   Wed, 15 Jul 2026 12:04:31 +0000   KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure     False   Wed, 15 Jul 2026 12:04:31 +0000   KubeletHasNoDiskPressure     kubelet has no disk pressure
  PIDPressure      False   Wed, 15 Jul 2026 12:04:31 +0000   KubeletHasSufficientPID      kubelet has sufficient PID available
  Ready            True    Wed, 15 Jul 2026 12:04:31 +0000   KubeletReady                 kubelet is posting ready status
Capacity:
  cpu:                8
  memory:             65049088Ki
  pods:               58
Allocatable:
  cpu:                7910m
  memory:             63816192Ki
  pods:               58
System Info:
  Kernel Version:             6.1.141-155.222.amzn2023.x86_64
  OS Image:                   Amazon Linux 2023.7.20250623
  Container Runtime Version:  containerd://1.7.27
  Kubelet Version:            v1.32.3-eks-473151a
Non-terminated Pods:          (4 in total)
  Namespace     Name                                CPU Requests  CPU Limits  Memory Requests  Memory Limits  Age
  ---------     ----                                ------------  ----------  ---------------  -------------  ---
  dagster       {AGENT_POD}   250m (3%)     1 (12%)     512Mi (0%)       1Gi (1%)       9d
  kube-system   aws-node-7hx2d                      25m (0%)      0 (0%)      0 (0%)           0 (0%)         21d
  kube-system   kube-proxy-q8l4n                    100m (1%)     0 (0%)      0 (0%)           0 (0%)         21d
  monitoring    node-exporter-5mrk9                 100m (1%)     200m (2%)   30Mi (0%)        50Mi (0%)      21d
Events:                       <none>"""


def _find_run(name: str) -> Optional[Run]:
    for run in RUNS:
        if name in (run.step_job, run.step_pod, run.run_job, run.run_pod):
            return run
    return None


@mcp.tool()
def kubectl_get_by_kind_in_namespace(kind: str, namespace: str) -> str:
    """Run `kubectl get <kind> -n <namespace> -o wide`. Use namespace "all" for all namespaces."""
    kind = _kind(kind)
    if namespace not in (NS, "all"):
        return f"No resources found in {namespace} namespace."
    if kind == "pod":
        return _get_pods()
    if kind == "job":
        return _get_jobs()
    if kind == "event":
        return _events_table(_all_events())
    if kind == "deployment":
        return (
            "NAME                     READY   UP-TO-DATE   AVAILABLE   AGE\n"
            "dagster-cloud-agent      1/1     1            1           412d\n"
            "dagster-code-warehouse   1/1     1            1           388d"
        )
    return f"No resources found in {namespace} namespace."


@mcp.tool()
def kubectl_get_by_name(kind: str, name: str, namespace: str = NS) -> str:
    """Run `kubectl get <kind> <name> -n <namespace> -o wide`."""
    kind = _kind(kind)
    if kind == "pod" and name in LIVE_PODS:
        return "\n".join(
            [_get_pods().splitlines()[0]]
            + [line for line in _get_pods().splitlines() if line.startswith(name)]
        )
    if kind == "job":
        lines = [
            line for line in _get_jobs().splitlines()[1:] if line.split()[0] == name
        ]
        if lines:
            return "\n".join([_get_jobs().splitlines()[0]] + lines)
    if kind == "node" and name == NODE:
        return f"NAME                                        STATUS   ROLES    AGE   VERSION\n{NODE}   Ready    <none>   21d   v1.32.3-eks-473151a"
    return _not_found(kind, name)


@mcp.tool()
def kubectl_describe(kind: str, name: str, namespace: str = NS) -> str:
    """Run `kubectl describe <kind> <name> -n <namespace>`."""
    kind = _kind(kind)
    run = _find_run(name)
    if kind == "job" and run and name == run.step_job:
        return _job_describe(run)
    if kind == "node" and name == NODE:
        return _node_describe(name)
    if kind == "job" and run and name == run.run_job:
        return f"Name:             {name}\nNamespace:        {NS}\nPods Statuses:    0 Active (0 Ready) / 1 Succeeded / 0 Failed\nConditions:\n  Type     Status\n  Complete True"
    return _not_found(kind, name)


@mcp.tool()
def kubectl_get_yaml(kind: str, name: str, namespace: str = NS) -> str:
    """Run `kubectl get <kind> <name> -n <namespace> -o yaml`."""
    kind = _kind(kind)
    run = _find_run(name)
    if kind == "job" and run and name == run.step_job:
        return _job_yaml(run)
    return _not_found(kind, name)


@mcp.tool()
def kubectl_events(resource_type: str, resource_name: str, namespace: str = NS) -> str:
    """Run `kubectl events --for <resource_type>/<resource_name> -n <namespace>`."""
    kind = _kind(resource_type)
    run = _find_run(resource_name)
    if not run:
        return f"No events found in {namespace} namespace."
    events = []
    if kind == "job" and resource_name == run.step_job:
        events = _job_events(run)
    elif kind == "pod" and resource_name == run.step_pod:
        events = _pod_events(run)
    elif resource_name in (run.run_job, run.run_pod):
        events = _run_pod_events(run)
    return (
        _events_table(events)
        if events
        else f"No events found in {namespace} namespace."
    )


@mcp.tool()
def kubectl_find_resource(kind: str, keyword: str) -> str:
    """Run `kubectl get <kind> -A -o wide | grep <keyword>`."""
    kind = _kind(kind)
    source = {"pod": _get_pods(), "job": _get_jobs()}.get(kind, "")
    hits = [f"{NS:<10} {line}" for line in source.splitlines()[1:] if keyword in line]
    return "\n".join(hits) if hits else f"No {kind} matching '{keyword}' found."


@mcp.tool()
def kubectl_logs(pod_name: str, namespace: str = NS) -> str:
    """Run `kubectl logs <pod_name> -n <namespace>` for the current container."""
    if pod_name == AGENT_POD:
        return "\n".join(
            f"{_iso(run.start - timedelta(seconds=3))} dagster_cloud_agent - INFO - Launching run {run.run_id} in Kubernetes job {run.run_job}"
            for run in RUNS
        )
    if pod_name in LIVE_PODS:
        return ""
    return _not_found("pod", pod_name)


@mcp.tool()
def kubectl_previous_logs(pod_name: str, namespace: str = NS) -> str:
    """Run `kubectl logs <pod_name> -n <namespace> --previous`."""
    if pod_name in LIVE_PODS:
        return f'Error from server (BadRequest): previous terminated container "dagster" in pod "{pod_name}" not found'
    return _not_found("pod", pod_name)


@mcp.tool()
def kubectl_top_pods(namespace: str = NS) -> str:
    """Run `kubectl top pods -n <namespace>`."""
    return f"NAME                                     CPU(cores)   MEMORY(bytes)\n{AGENT_POD}       21m          318Mi\n{CODE_POD}    4m           402Mi"


# --------------------------------------------------------------------------
# Loki
# --------------------------------------------------------------------------


def _dagster_line(
    ts: datetime,
    run: Run,
    step: Optional[str],
    event: str,
    message: str,
    level: str = "DEBUG",
) -> str:
    return f"{ts.strftime('%Y-%m-%d %H:%M:%S')} +0000 - dagster - {level} - {JOB} - {run.run_id} - {step or ''} - {event} - {message}"


def _step_lines(run: Run) -> List[Tuple[datetime, str]]:
    base = run.container_started

    def spark_ts(delta: float) -> str:
        return (base + timedelta(seconds=delta)).strftime("%y/%m/%d %H:%M:%S")

    return [
        (
            base + timedelta(seconds=2.1),
            _dagster_line(
                base + timedelta(seconds=2),
                run,
                STEP,
                "STEP_WORKER_STARTED",
                f'Executing step "{STEP}" in Kubernetes job {run.step_job}.',
            ),
        ),
        (
            base + timedelta(seconds=3.4),
            _dagster_line(
                base + timedelta(seconds=3),
                run,
                STEP,
                "STEP_START",
                f'Started execution of step "{STEP}".',
            ),
        ),
        (base + timedelta(seconds=12.8), 'Setting default log level to "WARN".'),
        (
            base + timedelta(seconds=12.9),
            "Using Spark's default log4j profile: org/apache/spark/log4j2-defaults.properties",
        ),
        (
            base + timedelta(seconds=13.0),
            "To adjust logging level use sc.setLogLevel(newLevel). For SparkR, use setLogLevel(newLevel).",
        ),
        (
            base + timedelta(seconds=15.2),
            f"{spark_ts(15)} WARN NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable",
        ),
        (
            base + timedelta(seconds=19.7),
            _dagster_line(
                base + timedelta(seconds=19),
                run,
                STEP,
                "LOADED_INPUT",
                'Loaded input "raw_events" using input manager "s3_parquet_io_manager"',
                level="DEBUG",
            ),
        ),
        (
            base + timedelta(seconds=20.3),
            f"{spark_ts(20)} WARN package: Truncated the string representation of a plan since it was too large. This behavior can be adjusted by setting 'spark.sql.debug.maxToStringFields'.",
        ),
        (
            base + timedelta(seconds=35.453),
            "/opt/app/pipelines/events/sessionize.py:212: UserWarning: Sort order is not guaranteed after repartition; window results may vary between runs.",
        ),
        (base + timedelta(seconds=35.454), "  warnings.warn("),
    ]


def _run_worker_lines(run: Run) -> List[Tuple[datetime, str]]:
    s = run.start
    return [
        (
            s + timedelta(seconds=4),
            _dagster_line(
                s + timedelta(seconds=4),
                run,
                None,
                "RUN_START",
                f'Started execution of run for "{JOB}".',
            ),
        ),
        (
            s + timedelta(seconds=5),
            _dagster_line(
                s + timedelta(seconds=5),
                run,
                None,
                "ENGINE_EVENT",
                "Executing steps using k8s executor",
            ),
        ),
        (
            run.step_launched,
            _dagster_line(
                run.step_launched,
                run,
                STEP,
                "STEP_WORKER_STARTING",
                f'Executing step "{STEP}" in Kubernetes job {run.step_job}.',
            ),
        ),
        (
            run.end - timedelta(seconds=3),
            _dagster_line(
                run.end - timedelta(seconds=3),
                run,
                STEP,
                "ENGINE_EVENT",
                f"Discovered failed Kubernetes job {run.step_job} for step {STEP}.",
                level="ERROR",
            ),
        ),
        (
            run.end - timedelta(seconds=3),
            _dagster_line(
                run.end - timedelta(seconds=3),
                run,
                STEP,
                "STEP_FAILURE",
                f'Execution of step "{STEP}" failed.',
                level="ERROR",
            ),
        ),
        (
            run.end - timedelta(seconds=2),
            _dagster_line(
                run.end - timedelta(seconds=2),
                run,
                DOWNSTREAM,
                "STEP_SKIPPED",
                f'Skipping step {DOWNSTREAM} due to skipped dependencies: ["{STEP}"].',
            ),
        ),
        (
            run.end,
            _dagster_line(
                run.end,
                run,
                None,
                "RUN_FAILURE",
                f"Execution of run for \"{JOB}\" failed. Steps failed: ['{STEP}'].",
                level="ERROR",
            ),
        ),
    ]


def _cni_lines(run: Run) -> List[Tuple[datetime, str]]:
    deleted = run.killed + timedelta(seconds=2.686)
    return [
        (
            run.step_launched + timedelta(seconds=0.4),
            f'{{"level":"info","ts":"{_iso(run.step_launched)}","caller":"rpc/rpc.pb.go:863","msg":"Received AddNetworkRequest: ClientVersion:\\"v1.19.5\\" K8S_POD_NAME:\\"{run.step_pod}\\" K8S_POD_NAMESPACE:\\"{NS}\\" NetworkName:\\"aws-cni\\" IfName:\\"eth0\\""}}',
        ),
        (
            deleted,
            f'{{"level":"info","ts":"{_iso(deleted)}","caller":"rpc/rpc.pb.go:881","msg":"Received DelNetworkRequest: ClientVersion:\\"v1.19.5\\" K8S_POD_NAME:\\"{run.step_pod}\\" K8S_POD_NAMESPACE:\\"{NS}\\" NetworkName:\\"aws-cni\\" IfName:\\"eth0\\" Reason:\\"PodDeleted\\""}}',
        ),
    ]


def _streams() -> List[Tuple[Dict[str, str], List[Tuple[datetime, str]]]]:
    streams = []
    for run in RUNS:
        common = {
            "namespace": NS,
            "container": "dagster",
            "dagster_run_id": run.run_id,
            "dagster_job": JOB,
            "node_name": NODE,
        }
        streams.append(
            (
                {
                    **common,
                    "pod": run.step_pod,
                    "dagster_op": STEP,
                    "k8s_app_component": "step_worker",
                },
                _step_lines(run),
            )
        )
        streams.append(
            (
                {**common, "pod": run.run_pod, "k8s_app_component": "run_worker"},
                _run_worker_lines(run),
            )
        )
        streams.append(
            (
                {
                    "namespace": "kube-system",
                    "pod": "aws-node-7hx2d",
                    "container": "aws-node",
                    "node_name": NODE,
                },
                _cni_lines(run),
            )
        )
    agent = {
        "namespace": NS,
        "pod": AGENT_POD,
        "container": "dagster-cloud-agent",
        "node_name": NODE,
    }
    streams.append(
        (
            agent,
            [
                (
                    run.start - timedelta(seconds=3),
                    f"dagster_cloud_agent - INFO - Launching run {run.run_id} in Kubernetes job {run.run_job}",
                )
                for run in RUNS
            ],
        )
    )
    return streams


_MATCHER = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"')
_LINE_FILTER = re.compile(r'(\|=|!=|\|~|!~)\s*"((?:[^"\\]|\\.)*)"')


def _labels_match(labels: Dict[str, str], selector: str) -> bool:
    for name, op, value in _MATCHER.findall(selector):
        value = value.encode().decode("unicode_escape")
        actual = labels.get(name, "")
        if op == "=" and actual != value:
            return False
        if op == "!=" and actual == value:
            return False
        if op == "=~" and not re.fullmatch(value, actual):
            return False
        if op == "!~" and re.fullmatch(value, actual):
            return False
    return True


def _line_match(line: str, pipeline: str) -> bool:
    for op, value in _LINE_FILTER.findall(pipeline):
        value = value.encode().decode("unicode_escape")
        if op == "|=" and value not in line:
            return False
        if op == "!=" and value in line:
            return False
        if op == "|~" and not re.search(value, line):
            return False
        if op == "!~" and re.search(value, line):
            return False
    return True


def _parse_ts(value: str) -> datetime:
    value = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", value):
        number = float(value)
        if number > 1e17:
            number /= 1e9
        elif number > 1e14:
            number /= 1e6
        elif number > 1e11:
            number /= 1e3
        return datetime.fromtimestamp(number, tz=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@mcp.tool()
def loki_query(query: str, start: str, end: str, limit: int = 100) -> str:
    """Query Loki logs with a LogQL log query (stream selector plus optional line filters).
    start/end are RFC3339 timestamps or unix epoch seconds. Returns matching log lines, newest first."""
    match = re.match(r"\s*\{([^}]*)\}(.*)", query, re.S)
    if not match:
        return f"parse error: queries must start with a stream selector, got {query!r}"
    selector, pipeline = match.groups()
    start_ts, end_ts = _parse_ts(start), _parse_ts(end)
    lines = []
    for labels, entries in _streams():
        if not _labels_match(labels, selector):
            continue
        for ts, line in entries:
            if start_ts <= ts <= end_ts and _line_match(line, pipeline):
                lines.append({"timestamp": _iso(ts), "labels": labels, "line": line})
    lines.sort(key=lambda entry: entry["timestamp"], reverse=True)
    if not lines:
        return f"No logs found for query {query!r} between {_iso(start_ts)} and {_iso(end_ts)}."
    return json.dumps(lines[:limit], indent=1)


@mcp.tool()
def loki_label_values(label: str) -> str:
    """List the values of a Loki label across all streams."""
    values = sorted({labels[label] for labels, _ in _streams() if label in labels})
    return json.dumps(values)


# --------------------------------------------------------------------------
# Prometheus (scrape interval 30s)
# --------------------------------------------------------------------------


def _memory_samples(run: Run) -> List[Tuple[datetime, float]]:
    first = run.container_started + timedelta(seconds=6)
    return [
        (first, 214_958_080),
        (first + timedelta(seconds=30), 1_995_419_648),
    ]


def _series() -> List[Tuple[Dict[str, str], List[Tuple[datetime, float]], datetime]]:
    """(labels, samples, end of series)."""
    series = []
    for run in RUNS:
        pod = {
            "namespace": NS,
            "pod": run.step_pod,
            "container": "dagster",
            "cluster": "prod-eu-1",
        }
        stale = run.killed + timedelta(seconds=28)
        samples = _memory_samples(run)
        series.append(
            (
                {"__name__": "container_memory_working_set_bytes", **pod, "node": NODE},
                samples,
                stale,
            )
        )
        series.append(
            (
                {"__name__": "container_memory_rss", **pod, "node": NODE},
                [(t, v * 0.93) for t, v in samples],
                stale,
            )
        )
        lifetime = [(run.container_started + timedelta(seconds=s), 0) for s in (6, 36)]
        series.append(
            (
                {
                    "__name__": "kube_pod_container_resource_limits",
                    **pod,
                    "resource": "memory",
                    "unit": "byte",
                },
                [(t, 8 * GIB) for t, _ in lifetime],
                stale,
            )
        )
        series.append(
            (
                {
                    "__name__": "kube_pod_container_resource_requests",
                    **pod,
                    "resource": "memory",
                    "unit": "byte",
                },
                [(t, 4 * GIB) for t, _ in lifetime],
                stale,
            )
        )
        series.append(
            (
                {"__name__": "kube_pod_container_status_restarts_total", **pod},
                [(t, 0) for t, _ in lifetime],
                stale,
            )
        )
        series.append(
            (
                {
                    "__name__": "kube_job_status_failed",
                    "namespace": NS,
                    "job_name": run.step_job,
                    "cluster": "prod-eu-1",
                },
                [
                    (run.job_failed + timedelta(seconds=s), 1)
                    for s in range(11, 3600 * 6, 30)
                ],
                NOW,
            )
        )
    return series


_SELECTOR = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\{[^}]*\})?")


def _select(
    query: str,
) -> List[Tuple[Dict[str, str], List[Tuple[datetime, float]], datetime]]:
    known = {labels["__name__"] for labels, _, _ in _series()}
    for name, matchers in _SELECTOR.findall(query):
        if name in known:
            return [
                s
                for s in _series()
                if s[0]["__name__"] == name and _labels_match(s[0], matchers or "")
            ]
    return []


def _value_at(samples, stale: datetime, at: datetime) -> Optional[float]:
    if at >= stale:
        return None
    previous = [v for t, v in samples if t <= at and at - t <= timedelta(minutes=5)]
    return previous[-1] if previous else None


def _range_window(query: str) -> Optional[timedelta]:
    match = re.search(r"\[(\d+)([smhd])\]", query)
    if not match:
        return None
    return timedelta(
        seconds=int(match.group(1))
        * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    )


@mcp.tool()
def prometheus_range_query(query: str, start: str, end: str, step: str = "30s") -> str:
    """Run a PromQL range query (Prometheus /api/v1/query_range). start/end are RFC3339 or unix seconds."""
    start_ts, end_ts = _parse_ts(start), _parse_ts(end)
    step_seconds = float(re.sub(r"[^0-9.]", "", step) or 30) * (
        60 if step.endswith("m") else 1
    )
    result = []
    for labels, samples, stale in _select(query):
        values = []
        at = start_ts
        while at <= end_ts:
            value = _value_at(samples, stale, at)
            if value is not None:
                values.append([at.timestamp(), str(int(value))])
            at += timedelta(seconds=step_seconds)
        if values:
            result.append({"metric": labels, "values": values})
    return json.dumps(
        {"status": "success", "data": {"resultType": "matrix", "result": result}}
    )


@mcp.tool()
def prometheus_instant_query(query: str, time: Optional[str] = None) -> str:
    """Run a PromQL instant query (Prometheus /api/v1/query). time is RFC3339 or unix seconds; defaults to now."""
    at = _parse_ts(time) if time else NOW
    window = _range_window(query)
    result = []
    for labels, samples, stale in _select(query):
        if window is not None:
            in_window = [v for t, v in samples if at - window <= t <= at]
            value = (
                max(in_window)
                if in_window and "max" in query
                else (in_window[-1] if in_window else None)
            )
        else:
            value = _value_at(samples, stale, at)
        if value is not None:
            result.append(
                {"metric": labels, "value": [at.timestamp(), str(int(value))]}
            )
    return json.dumps(
        {"status": "success", "data": {"resultType": "vector", "result": result}}
    )


# --------------------------------------------------------------------------
# Dagster+ GraphQL
# --------------------------------------------------------------------------


def _run_json(run: Run) -> Dict:
    return {
        "runId": run.run_id,
        "status": run.status,
        "startTime": run.start.timestamp(),
        "endTime": run.end.timestamp(),
        "pipelineName": JOB,
        "stepStats": [
            {
                "stepKey": STEP,
                "status": "FAILURE",
                "startTime": (run.container_started + timedelta(seconds=3)).timestamp(),
                "endTime": (run.end - timedelta(seconds=3)).timestamp(),
            },
            {
                "stepKey": DOWNSTREAM,
                "status": "SKIPPED",
                "startTime": None,
                "endTime": None,
            },
        ],
    }


def _run_events(run: Run) -> List[Dict]:
    failure = (run.end - timedelta(seconds=3)).timestamp()
    return [
        {
            "__typename": "RunStartEvent",
            "message": f'Started execution of run for "{JOB}".',
            "timestamp": str(int((run.start.timestamp() + 4) * 1000)),
            "stepKey": None,
        },
        {
            "__typename": "ExecutionStepStartEvent",
            "message": f'Started execution of step "{STEP}".',
            "timestamp": str(int((run.container_started.timestamp() + 3) * 1000)),
            "stepKey": STEP,
        },
        {
            "__typename": "EngineEvent",
            "message": f"Discovered failed Kubernetes job {run.step_job} for step {STEP}.",
            "timestamp": str(int(failure * 1000)),
            "stepKey": STEP,
        },
        {
            "__typename": "ExecutionStepFailureEvent",
            "message": f'Execution of step "{STEP}" failed.',
            "timestamp": str(int(failure * 1000)),
            "stepKey": STEP,
            "error": {
                "message": f"Discovered failed Kubernetes job {run.step_job} for step {STEP}.\n",
                "className": None,
                "stack": [],
                "causes": [],
            },
        },
        {
            "__typename": "ExecutionStepSkippedEvent",
            "message": f'Skipping step {DOWNSTREAM} due to skipped dependencies: ["{STEP}"].',
            "timestamp": str(int((failure + 1) * 1000)),
            "stepKey": DOWNSTREAM,
        },
        {
            "__typename": "RunFailureEvent",
            "message": f"Execution of run for \"{JOB}\" failed. Steps failed: ['{STEP}'].",
            "timestamp": str(int(run.end.timestamp() * 1000)),
            "stepKey": None,
        },
    ]


def _all_runs() -> List[Dict]:
    runs = [
        {
            "runId": r.run_id,
            "status": r.status,
            "startTime": r.start.timestamp(),
            "endTime": r.end.timestamp(),
            "pipelineName": JOB,
        }
        for r in reversed(RUNS)
    ]
    runs += [
        {
            "runId": run_id,
            "status": "SUCCESS",
            "startTime": s.timestamp(),
            "endTime": e.timestamp(),
            "pipelineName": JOB,
        }
        for run_id, s, e in reversed(YESTERDAY_RUNS)
    ]
    return runs


@mcp.tool()
def dagster_graphql_query(query: str) -> str:
    """POST a GraphQL query to the Dagster+ API at https://example.dagster.cloud/prod/graphql and return the JSON response."""
    run_ids = re.findall(r'runId:\s*"([^"]+)"', query)
    run = next((r for r in RUNS if run_ids and r.run_id == run_ids[0]), None)
    if "logsForRun" in query:
        if not run:
            return json.dumps(
                {
                    "data": {
                        "logsForRun": {
                            "__typename": "RunNotFoundError",
                            "message": f"Run {run_ids[0] if run_ids else ''} could not be found.",
                        }
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "logsForRun": {
                        "__typename": "EventConnection",
                        "events": _run_events(run),
                        "cursor": "6",
                        "hasMore": False,
                    }
                }
            }
        )
    if "runOrError" in query:
        if not run:
            return json.dumps(
                {
                    "data": {
                        "runOrError": {
                            "__typename": "RunNotFoundError",
                            "message": "Run not found",
                        }
                    }
                }
            )
        return json.dumps(
            {"data": {"runOrError": {"__typename": "Run", **_run_json(run)}}}
        )
    if "runsOrError" in query:
        runs = _all_runs()
        if re.search(r"statuses:\s*\[\s*FAILURE", query):
            runs = [r for r in runs if r["status"] == "FAILURE"]
        if (
            "limit:" in query
            and "filter" in query
            and re.search(r"filter:\s*\{[^}]*limit", query)
        ):
            return json.dumps(
                {
                    "errors": [
                        {
                            "message": 'Argument "filter" has invalid value. In field "limit": Unknown field.'
                        }
                    ]
                }
            )
        return json.dumps(
            {"data": {"runsOrError": {"__typename": "Runs", "results": runs}}}
        )
    return json.dumps(
        {
            "errors": [
                {
                    "message": "Syntax Error: Expected a root field: runsOrError, runOrError, logsForRun, ..."
                }
            ]
        }
    )


if __name__ == "__main__":
    mcp.run()
