"""Print two hours of cAdvisor-shaped history for one pod, as OpenMetrics.

The output is fed to `promtool tsdb create-blocks-from openmetrics`, so the
pod's CPU and memory series look as if the kubelet had been scraped while it
ran. The labels match what the `kubelet` scrape job in prometheus.yaml produces.

Usage: generate_history.py <namespace> <pod> <container> <image> <node> <end_epoch>
"""

import hashlib
import random
import sys
import uuid

namespace, pod, container, image, node, end = sys.argv[1:7]
end_ts = int(end)

# (seconds before end, CPU millicores, memory MiB): a baseline, a small spike
# 90 minutes ago, a larger one 40 minutes ago, then settling down.
SHAPE = [
    (7200, 250, 512),
    (6900, 280, 520),
    (6600, 265, 515),
    (6300, 290, 525),
    (5400, 320, 540),
    (5100, 450, 580),
    (4800, 480, 590),
    (4500, 350, 550),
    (3600, 270, 520),
    (3300, 285, 525),
    (3000, 260, 518),
    (2700, 295, 530),
    (2400, 550, 620),
    (2100, 680, 680),
    (1800, 720, 700),
    (1500, 650, 660),
    (1200, 420, 580),
    (900, 380, 560),
    (600, 320, 540),
    (300, 300, 535),
    (0, 290, 530),
]
STEP = 30

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


def interpolate(offset):
    for (o1, c1, m1), (o2, c2, m2) in zip(SHAPE, SHAPE[1:]):
        if o2 <= offset <= o1:
            frac = (o1 - offset) / (o1 - o2)
            return c1 + (c2 - c1) * frac, m1 + (m2 - m1) * frac
    return SHAPE[-1][1], SHAPE[-1][2]


rng = random.Random(digest)
cpu_lines, mem_lines = [], []
cpu_seconds = 0.0
start_ts = end_ts - SHAPE[0][0]
for ts in range(start_ts, end_ts + 1, STEP):
    millicores, mib = interpolate(end_ts - ts)
    millicores *= rng.uniform(0.92, 1.08)
    mib *= rng.uniform(0.99, 1.01)
    cpu_seconds += millicores / 1000 * STEP
    cpu_lines.append(
        f'container_cpu_usage_seconds_total{{cpu="total",{labels}}} {cpu_seconds:.6f} {ts}'
    )
    mem_lines.append(
        f"container_memory_working_set_bytes{{{labels}}} {int(mib * 1024 * 1024)} {ts}"
    )

print("\n".join(cpu_lines))
print("\n".join(mem_lines))
print("# EOF")
