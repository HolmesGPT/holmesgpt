# Service Connectivity Issues

## Overview
Troubleshooting Kubernetes service connectivity problems.

## Investigation Steps

### 1. Service Configuration
```bash
kubectl get svc {service_name} -n {namespace} -o yaml
kubectl get endpoints {service_name} -n {namespace}
```

### 2. Backend Pods
```bash
kubectl get pods -n {namespace} -l {selector}
```

### 3. Test Connectivity
```bash
kubectl run -it --rm debug --image=busybox --restart=Never -- wget -O- {service_name}:{port}
```

### 4. Network Policies
```bash
kubectl get networkpolicies -n {namespace}
```

## Common Service Issues
- No endpoints (no matching pods)
- Selector mismatch
- Wrong target port
- Network policy blocking
- Service type misconfiguration
