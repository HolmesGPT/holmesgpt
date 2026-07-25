# Apache Airflow

Connect HolmesGPT to the Airflow Stable REST API.

## Prerequisites

- A reachable Airflow API server
- Read access to DAGs, DAG runs, task instances, and task logs

## Configuration

Airflow 3 uses API v2:

```yaml-toolset-config
toolsets:
  airflow:
    enabled: true
    config:
      api_url: https://airflow.example.com
      api_version: v2
      bearer_token: "{{ env.AIRFLOW_TOKEN }}"
      max_items: 100
      max_log_characters: 20000
```

For Airflow 2, set `api_version: v1`. Basic authentication is available through
`username` and `password`.

## Multiple Instances

```multi-instance
toolset: airflow
name: Apache Airflow
config: |
  api_url: https://airflow.example.com
  api_version: v2
  bearer_token: "{{ env.AIRFLOW_TOKEN }}"
```

## Common Use Cases

```text
Find the failed tasks in the latest orders DAG run
```

```text
Read the failed task attempt log and identify the root cause
```
