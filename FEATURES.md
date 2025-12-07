
# Features

This page document and describes HolmesGPT's behaviour when it comes to its features.


## Root Cause Analysis

Also called Investigation, Root Cause Analysis (RCA) is HolmesGPT's ability to investigate alerts,
typically from Prometheus' alert manager.

### Sectioned output

HolmesGPT generates structured output by default. It is also capable of generating sections based on request.

Here is an example of a request payload to run an investigation:

```json
{
  "source": "prometheus",
  "source_instance_id": "some-instance",
  "title": "Pod is crash looping.",
  "description": "Pod default/oomkill-deployment-696dbdbf67-d47z6 (main2) is in waiting state (reason: 'CrashLoopBackOff').",
  "subject": {
    "name": "oomkill-deployment-696dbdbf67-d47z6",
    "subject_type": "deployment",
    "namespace": "default",
    "node": "some-node",
    "container": "main2",
    "labels": {
      "x": "y",
      "p": "q"
    },
    "annotations": {}
  },
  "context":
    {
      "robusta_issue_id": "5b3e2fb1-cb83-45ea-82ec-318c94718e44"
    },
  "include_tool_calls": true,
  "include_tool_call_results": true
  "sections":  {
    "Alert Explanation": "1-2 sentences explaining the alert itself - note don't say \"The alert indicates a warning event related to a Kubernetes pod doing blah\" rather just say \"The pod XYZ did blah\" because that is what the user actually cares about",
    "Conclusions and Possible Root causes": "What conclusions can you reach based on the data you found? what are possible root causes (if you have enough conviction to say) or what uncertainty remains. Don't say root cause but 'possible root causes'. Be clear to distinguish between what you know for certain and what is a possible explanation",
    "Related logs": "Truncate and share the most relevant logs, especially if these explain the root cause. For example: \nLogs from pod robusta-holmes:\n```\n<logs>```\n. Always embed the surroundding +/- 5 log lines to any relevant logs. "
  }
}
```

Notice that the "sections" field contains 3  different sections. The text value for each section should be a prompt telling the LLM what the section should contain.
You can then expect the following in return:

```
{
  "analysis": <monolithic text response. Contains all the sections aggregated together>,
  "sections": {
    "Alert Explanation": <A markdown text with the explanation of the alert>,
    "Conclusions and Possible Root causes": <Conclusions reached by the LLM>,
    "Related logs": <Any related logs the LLM could find through tools>
  },
  "tool_calls": <tool calls>,
  "instructions": <Specific instructions used for this investigation>
}
```

In some cases, the LLM may decide to set a section to `null` or even add or ignore some sections.


## PromQL

If the `prometheus/metrics` toolset is enabled, HolmesGPT can generate embed graphs in conversations (ask holmes).

For example, here is scenario in which the LLM answers with a graph:


User question:

```
Show me the http request latency over time for the service customer-orders-service?
```


# OLD

<< {"type": "promql", "tool_name": "execute_prometheus_range_query", "random_key": "ABCXYZ"} >>

```json
"tool_calls": [
  {
    "tool_call_id": "call_lKI7CQW6Y2n1ZQ5dlxX79TcM",
    "tool_name": "execute_prometheus_range_query",
    "description": "Prometheus query_range. query=rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m]), start=1739705559, end=1739791959, step=300, description=HTTP request latency for customer-orders-service",
    "result": "{\n  \"status\": \"success\",\n  \"random_key\": \"ABCXYZ\",\n  \"tool_name\": \"execute_prometheus_range_query\",\n  \"description\": \"Average HTTP request latency for customer-orders-service\",\n  \"query\": \"rate(http_request_duration_seconds_sum{service=\\\"customer-orders-service\\\"}[5m]) / rate(http_request_duration_seconds_count{service=\\\"customer-orders-service\\\"}[5m])\",\n  \"start\": \"1739705559\",\n  \"end\": \"1739791959\",\n  \"step\": 60\n}"
  }
],
```

The result of this tool call contains details about the [prometheus query](https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries) to build the graph returned by HolmesGPT:

```json

  {
    "tool_call_id": "call_lKI7CQW6Y2n1ZQ5dlxX79TcM",
    "tool_name": "execute_prometheus_range_query",
    "description": "Prometheus query_range. query=rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m]), start=1739705559, end=1739791959, step=300, description=HTTP request latency for customer-orders-service",
    "result":{
      "status": "success",
      "random_key": "ABCXYZ",
      "tool_name": "execute_prometheus_range_query",
      "description": "Average HTTP request latency for customer-orders-service",
      "query": "rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m])",
      "start": "1739705559", // Can be rfc3339 or a unix timestamp
      "end": "1739791959", // Can be rfc3339 or a unix timestamp
      "step": 60 // Query resolution step width in seconds
    }
}


# NEW

HolmesGPT text response:
```
Here's the average HTTP request latency over time for the `customer-orders-service`:

<< {"type": "promql", "tool_name": "execute_prometheus_range_query", "tool_call_id": "call_lKI7CQW6Y2n1ZQ5dlxX79TcM"} >>
```

In addition to this text response, the returned JSON will contain one or more tool calls, including the prometheus query:

```json
"tool_calls": [
  {
    "tool_call_id": "call_lKI7CQW6Y2n1ZQ5dlxX79TcM",
    "tool_name": "execute_prometheus_range_query",
    "description": "Prometheus query_range. query=rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m]), start=1739705559, end=1739791959, step=300, description=HTTP request latency for customer-orders-service",
    "result": "{\n  \"status\": \"success\",\n  \"tool_name\": \"execute_prometheus_range_query\",\n  \"description\": \"Average HTTP request latency for customer-orders-service\",\n  \"query\": \"rate(http_request_duration_seconds_sum{service=\\\"customer-orders-service\\\"}[5m]) / rate(http_request_duration_seconds_count{service=\\\"customer-orders-service\\\"}[5m])\",\n  \"start\": \"1739705559\",\n  \"end\": \"1739791959\",\n  \"step\": 60\n}"
  }
],
```

The result of this tool call contains details about the [prometheus query](https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries) to build the graph returned by HolmesGPT:

```json

  {
    "tool_call_id": "call_lKI7CQW6Y2n1ZQ5dlxX79TcM",
    "tool_name": "execute_prometheus_range_query",
    "description": "Prometheus query_range. query=rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m]), start=1739705559, end=1739791959, step=300, description=HTTP request latency for customer-orders-service",
    "result":{
      "status": "success",
      "tool_name": "execute_prometheus_range_query",
      "description": "Average HTTP request latency for customer-orders-service",
      "query": "rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m])",
      "start": "1739705559", // Can be rfc3339 or a unix timestamp
      "end": "1739791959", // Can be rfc3339 or a unix timestamp
      "step": 60 // Query resolution step width in seconds
    }
}
```

In addition to `execute_prometheus_range_query`, HolmesGPT can generate similar results with an `execute_prometheus_instant_query` which is an [instant query](https://prometheus.io/docs/prometheus/latest/querying/api/#instant-queries):

```
Here's the average HTTP request latency over time for the `customer-orders-service`:

<< {"type": "promql", "tool_name": "execute_prometheus_instant_query", "tool_call_id": "9kLK"} >>
```

```json
{
  "status": "success",
  "tool_call_id": "2KiL",
  "tool_name": "execute_prometheus_instant_query",
  "description": "Average HTTP request latency for customer-orders-service",
  "query": "rate(http_request_duration_seconds_sum{service=\"customer-orders-service\"}[5m]) / rate(http_request_duration_seconds_count{service=\"customer-orders-service\"}[5m])"
}
```

Unlike the range query, the instant query result lacks the `start`, `end` and `step` arguments.
