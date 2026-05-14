---
name: kubernetes-troubleshooting
description: Investigate Kubernetes issues — pod crashes, pending/CrashLoopBackOff pods, deployment/replicaset/statefulset problems, ingress/service misconfigurations, node issues, application runtime errors, or any question that requires kubectl. Fetch this skill whenever the user mentions kubectl, pods, deployments, replicasets, namespaces, services, ingresses, nodes, or otherwise refers to a Kubernetes cluster.
---

# Kubernetes Investigation

Follow this skill whenever troubleshooting an issue on Kubernetes, or when the user's question requires kubectl.

## Workflow

* run as many kubectl commands as you need to gather more information, then respond.
* if possible, do so repeatedly on different Kubernetes objects.
* for example, for deployments first run kubectl on the deployment then a replicaset inside it, then a pod inside that.
* when investigating a pod that crashed or application errors, always run kubectl_describe and fetch the logs
* Do check both the status of the kubernetes resources and the application runtime as well, by investigating logs
* do not give an answer like "The pod is pending" as that doesn't state why the pod is pending and how to fix it.
* do not give an answer like "Pod's node affinity/selector doesn't match any available nodes" because that doesn't include data on WHICH label doesn't match
* if investigating an issue on many pods, there is no need to check more than 3 individual pods in the same deployment. pick up to a representative 3 from each deployment if relevant
* if the user says something isn't working, ALWAYS:
** use kubectl_describe on the owner workload + individual pods and look for any transient issues they might have been referring to
** look for misconfigured ingresses/services etc
** check the application logs because there may be runtime issues

## Special cases

* If asked to port forward, find out the app or pod port (kubectl describe) and provide a port forward command specific to the user's question.

## Parallel tool calls

When multiple kubectl investigations are independent, issue them in parallel — e.g. `kubectl_logs` + `kubectl_describe` + `kubectl_get` simultaneously rather than sequentially.

## Worked Example

User: Why did the webserver-example app crash?
(Call tool `kubectl_find_resource kind=pod keyword=webserver`)
(Call tool `kubectl_previous_logs namespace=demos pod=webserver-example-1299492-d9g9d`  # this pod name was found from the previous tool call)

AI: `webserver-example-1299492-d9g9d` crashed due to email validation error during HTTP request for /api/create_user
Relevant logs:

```
2021-01-01T00:00:00.000Z [ERROR] Missing required field 'email' in request body
```

Validation error led to unhandled Java exception causing a crash.
