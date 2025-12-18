# Python Application Memory Troubleshooting

## Overview
This runbook helps diagnose memory issues in Python applications running in Kubernetes.

## Investigation Steps

### 1. Check Application Status
Check if the application pods are running and review recent events:
```bash
kubectl get pods -n {namespace} -l app={app_name}
kubectl describe pod -n {namespace} -l app={app_name}
kubectl top pod -n {namespace} -l app={app_name}
```

### 2. Review Application Logs
Look for memory errors, GC warnings, or memory-related messages:
```bash
kubectl logs -n {namespace} -l app={app_name} --tail=100 | grep -E "(memory|Memory|ERROR|WARNING|GC|Cache|processed)"
```

Pay attention to:
- Processing patterns and batch sizes
- Cache growth indicators
- Memory allocation patterns

### 3. Analyze Application Code Patterns
Look for common memory leak patterns in logs:
```bash
# Check for growing collections or caches
kubectl logs -n {namespace} -l app={app_name} | grep -E "size:|count:|total:|cache"

# Look for processing patterns
kubectl logs -n {namespace} -l app={app_name} | grep -E "Processing|processed|Batch"
```

Common memory leak indicators:
- Continuously growing collections (lists, dicts)
- Caches without eviction policies
- Circular references preventing garbage collection

### 4. Identify Memory Growth Patterns
Analyze how memory usage correlates with application activity:
```bash
# Monitor memory growth over time
kubectl top pod -n {namespace} -l app={app_name} --use-protocol-buffers

# Correlate with processing activity
kubectl logs -n {namespace} -l app={app_name} --tail=50
```

### 5. Monitor Memory Metrics
Check memory usage patterns over time:
```bash
# Get current memory usage
kubectl top pod -n {namespace} -l app={app_name}

# Review resource limits
kubectl describe pod -n {namespace} -l app={app_name} | grep -A3 "Limits:"
```

## Python-Specific Memory Profiling

### Using Built-in Tools
If the application has profiling endpoints or debug mode:
```bash
# Check if app exposes memory profiling endpoints
kubectl exec -n {namespace} {pod_name} -- curl localhost:8000/debug/memory 2>/dev/null || echo "No debug endpoint"

# If the app uses Flask/Django debug toolbar
kubectl port-forward -n {namespace} {pod_name} 8000:8000
# Then visit http://localhost:8000/debug
```

### Memory Profiling with py-spy (if available in container)
```bash
# Check if py-spy is installed
kubectl exec -n {namespace} {pod_name} -- which py-spy

# If available, profile the running process
kubectl exec -n {namespace} {pod_name} -- py-spy dump --pid 1
```

### Using tracemalloc (if enabled in code)
Look for tracemalloc output in logs:
```bash
kubectl logs -n {namespace} -l app={app_name} | grep -E "tracemalloc|Top.*memory blocks"
```

### Memory Usage Patterns
Check for memory allocation patterns in logs:
```bash
# Look for object creation patterns
kubectl logs -n {namespace} -l app={app_name} | grep -E "Created|Allocated|New.*object"

# Check garbage collection activity
kubectl logs -n {namespace} -l app={app_name} | grep -E "gc\.|GC|garbage"
```

## Common Python Memory Leak Patterns

1. **Unbounded Collections**: Lists/dicts that grow without limits
   - Solution: Implement size limits or use collections.deque with maxlen
   - Profile: Look for growing collection sizes in logs

2. **Cache Without Eviction**: Caching data without removing old entries
   - Solution: Use functools.lru_cache or implement TTL-based eviction
   - Profile: Monitor cache size metrics in logs

3. **Keeping References to Large Objects**: Storing processed data unnecessarily
   - Solution: Process and discard, use generators for streaming
   - Profile: Check for accumulating processed items

4. **Global State Accumulation**: Module-level variables that accumulate data
   - Solution: Encapsulate in classes with proper cleanup
   - Profile: Look for module-level collections in stack traces

5. **Circular References**: Objects referencing each other preventing GC
   - Solution: Use weakref for one direction of the reference
   - Profile: Check for objects with high reference counts

6. **Large Object Allocation**: Creating unnecessarily large objects
   - Solution: Process data in chunks, use numpy arrays for numerical data
   - Profile: Look for large allocation warnings or patterns
