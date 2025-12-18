#!/usr/bin/env python3
import json
from datetime import datetime
import time


def generate_log_entry(memory_mb, gc_pause_ms=None, error=None):
    timestamp = datetime.utcnow()

    entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": "ERROR" if error else "WARN" if memory_mb > 3000 else "INFO",
        "service": "data-processor",
        "memory_usage_mb": memory_mb,
        "memory_limit_mb": 4096,
        "memory_percentage": round((memory_mb / 4096) * 100, 2),
    }

    if gc_pause_ms:
        entry["gc_pause_ms"] = gc_pause_ms
        entry["message"] = f"GC pause detected: {gc_pause_ms}ms"
    elif error:
        entry["error"] = error
        entry["message"] = error
    else:
        entry["message"] = (
            f"Memory usage: {memory_mb}MB ({entry['memory_percentage']}%)"
        )

    return json.dumps(entry)


def main():
    # Simulate gradual memory leak over time
    # Start at 100MB, grow to 4096MB (OOM)

    base_memory = 100
    records_per_gb = 25000  # Distribute memory growth across logs

    for i in range(100000):
        # Calculate current memory usage (exponential growth)
        memory_mb = int(base_memory + (i / records_per_gb) * 1000)

        # Cap at 4096MB
        memory_mb = min(4096, memory_mb)

        # GC pauses get longer as memory pressure increases
        if i % 1000 == 0 and memory_mb > 1000:
            gc_pause = int((memory_mb / 4096) * 500)  # Max 500ms GC pause
            print(generate_log_entry(memory_mb, gc_pause_ms=gc_pause))

        # Regular memory usage logs
        print(generate_log_entry(memory_mb))

        # Memory warnings as we approach limit
        if memory_mb > 3500 and i % 100 == 0:
            print(
                json.dumps(
                    {
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "level": "WARN",
                        "service": "data-processor",
                        "message": f"Memory usage critical: {memory_mb}MB of 4096MB ({round((memory_mb/4096)*100, 2)}%)",
                        "action": "Consider increasing memory limit or investigating memory leak",
                    }
                )
            )

        # OutOfMemoryError at the end
        if memory_mb == 4096 and i > 99000:
            print(
                generate_log_entry(
                    memory_mb, error="java.lang.OutOfMemoryError: Java heap space"
                )
            )
            print(
                json.dumps(
                    {
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "level": "ERROR",
                        "service": "data-processor",
                        "message": "Application crashed due to OutOfMemoryError",
                        "stack_trace": "java.lang.OutOfMemoryError: Java heap space\n\tat com.example.DataProcessor.processData(DataProcessor.java:142)\n\tat com.example.DataProcessor.main(DataProcessor.java:45)",
                    }
                )
            )
            break

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
