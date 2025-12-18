#!/usr/bin/env python3
import json
from datetime import datetime
import time
import random


def generate_log_entry(
    cache_ttl, cache_hits, cache_misses, message="Cache operation completed"
):
    timestamp = datetime.utcnow()
    hit_rate = (
        (cache_hits / (cache_hits + cache_misses) * 100)
        if (cache_hits + cache_misses) > 0
        else 0
    )

    entry = {
        "timestamp": timestamp.isoformat() + "Z",
        "level": "WARN" if hit_rate < 50 else "INFO",
        "service": "cache-service",
        "cache_stats": {
            "hits": cache_hits,
            "misses": cache_misses,
            "rate": round(hit_rate, 2),
        },
        "message": message,
    }

    # Only occasionally include the TTL in metadata
    if random.random() < 0.05:  # 5% chance
        entry["metadata"] = {"ttl": cache_ttl}

    return json.dumps(entry)


def main():
    # First 50,000 logs with normal cache TTL (3600 seconds = 1 hour)
    total_hits = 0
    total_misses = 0

    for i in range(50000):
        # Good cache performance with 1 hour TTL
        if random.random() < 0.85:  # 85% hit rate
            total_hits += 1
            hits = 1
        else:
            total_misses += 1
            hits = 0

        print(
            generate_log_entry(
                cache_ttl=3600,
                cache_hits=total_hits,
                cache_misses=total_misses,
                message=f"Cache {'hit' if hits else 'miss'} for key: {random.randint(1000, 9999)}",
            )
        )

    # Just a generic config reload message without details
    print(
        json.dumps(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "INFO",
                "service": "cache-service",
                "event": "configuration_reload",
                "message": "Configuration reloaded successfully",
            }
        )
    )

    # Next 50,000 logs with reduced cache TTL (60 seconds)
    # Reset counters to show the dramatic change
    total_hits = 0
    total_misses = 0

    for i in range(50000):
        # Poor cache performance with 60 second TTL
        if random.random() < 0.25:  # Only 25% hit rate now
            total_hits += 1
            hits = 1
        else:
            total_misses += 1
            hits = 0

        print(
            generate_log_entry(
                cache_ttl=60,
                cache_hits=total_hits,
                cache_misses=total_misses,
                message=f"Cache {'hit' if hits else 'miss'} for key: {random.randint(1000, 9999)}",
            )
        )

        # Add periodic warnings about poor cache performance
        if i % 1000 == 0 and i > 0:
            hit_rate = total_hits / (total_hits + total_misses) * 100
            if hit_rate < 30:
                print(
                    json.dumps(
                        {
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "level": "ERROR",
                            "service": "cache-service",
                            "message": "Performance degradation detected",
                            "metrics": {
                                "hit_rate": round(hit_rate, 2),
                                "total_operations": total_hits + total_misses,
                            },
                        }
                    )
                )

    # Keep pod running
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
