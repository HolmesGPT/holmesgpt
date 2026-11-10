"""Print 24 hours of cAdvisor-shaped history for one pod, as OpenMetrics.

The output is fed to `promtool tsdb create-blocks-from openmetrics`, so the
pod's CPU and memory series look as if the kubelet had been scraped for the
whole day. The labels match what the `kubelet` scrape job in prometheus.yaml
produces.

Usage: generate_history.py <namespace> <pod> <container> <image> <node> <end_epoch>
"""

import hashlib
import math
import random
import sys
import uuid

namespace, pod, container, image, node, end = sys.argv[1:7]
end_ts = int(end)
DURATION = 24 * 3600
STEP = 60

digest = hashlib.sha256(f"{namespace}/{pod}".encode()).hexdigest()
pod_uid = str(uuid.UUID(digest[:32]))
container_id = hashlib.sha256(f"{pod}/{container}".encode()).hexdigest()
labels = (
    f'container="{container}",'
    f'id="/kubepods/burstable/pod{pod_uid}/{container_id}",'
    f'image="{image}",'
    f'instance="{node}",'
    'job="kubelet",'
    'metrics_path="/metrics/cadvisor",'
    f'name="{container_id}",'
    f'namespace="{namespace}",'
    f'node="{node}",'
    f'pod="{pod}"'
)

rng = random.Random(digest)
cpu_lines, mem_lines = [], []
cpu_seconds = 0.0
for ts in range(end_ts - DURATION, end_ts + 1, STEP):
    hour = (ts % 86400) / 3600
    # Daytime load from alert traffic, plus a scheduled scan every 4 hours
    # that pins the container for about 10 minutes.
    cores = 0.06 + 0.05 * max(0.0, math.sin((hour - 6) / 24 * 2 * math.pi))
    if (ts % (4 * 3600)) < 600:
        cores += 0.35
    cores *= rng.uniform(0.85, 1.15)
    cpu_seconds += cores * STEP
    mib = 210 + 25 * math.sin(ts / 5400) + rng.uniform(-3, 3)
    cpu_lines.append(
        f'container_cpu_usage_seconds_total{{cpu="total",{labels}}} {cpu_seconds:.6f} {ts}'
    )
    mem_lines.append(
        f"container_memory_working_set_bytes{{{labels}}} {int(mib * 1024 * 1024)} {ts}"
    )

print("\n".join(cpu_lines))
print("\n".join(mem_lines))
print("# EOF")
