# Using HolmesGPT

This guide covers how to use HolmesGPT to investigate infrastructure and application issues across your project's environments.

## Getting Started

1. Go to the HolmesGPT URL provided by your admin.
2. Log in with your PDI Okta account.
3. Select a project from the sidebar. You will only see projects your Okta groups grant you access to.

Once you select a project, Holmes has access to that project's connected AWS accounts, monitoring tools (Grafana, Datadog), incident management (PagerDuty, Azure DevOps), and more.

## Asking Questions

Type a question in the chat box describing what you want to investigate. Holmes connects to your project's integrations and calls tools to gather real data before responding.

**AWS Infrastructure**

```
What EC2 instances are running and what's their health?
```

```
Are there any RDS instances with high CPU or storage issues?
```

```
Show me recent CloudWatch alarms that fired
```

```
What S3 buckets exist and do any have public access?
```

**Monitoring and Logs**

```
Check Grafana for any dashboards showing errors in the last hour
```

```
What does Datadog show for error rates on the payment service?
```

```
Show me application logs with errors from the last 30 minutes
```

**Incidents**

```
What PagerDuty incidents are open right now?
```

```
Find recent Azure DevOps work items related to deployment failures
```

```
Summarize the last 5 resolved PagerDuty incidents
```

**Cross-System Investigation**

```
We're seeing slow responses on the checkout API. Check CloudWatch metrics, application logs, and recent deployments.
```

```
The database seems overloaded. Check RDS metrics, active connections, and any related PagerDuty incidents.
```

## Understanding Responses

Holmes calls tools to gather data from your project's integrations. You will see the tool calls listed in the response so you can verify what data was queried.

Follow-up actions may appear as buttons below the response (Logs, Graphs, Related Issues). Click these to dig deeper without re-typing your question.

## Tips for Effective Prompts

- **Be specific** about what you are looking for. "CPU usage on prod EC2 instances" works better than "check servers."
- **Mention time ranges** when relevant. "CloudWatch alarms in the last 2 hours" narrows the search.
- **Name the service or resource** if you know it. "Check the order-service ECS task" is more targeted than "check ECS."
- **Ask cross-system questions** when troubleshooting. Holmes can correlate data across AWS, monitoring, and incident tools in a single response.
- **Holmes is read-only.** It investigates and reports but will not make changes to your infrastructure.
