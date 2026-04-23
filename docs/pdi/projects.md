# Projects & Integrations

A **project** is a named group of toolset instances that scopes what Holmes can access during an investigation. Each project has a tag filter that determines which instances are included, and users are assigned to projects with specific roles.

## Project Components

Each project consists of:

- **Name and description** -- identifies the project in the UI and webhook routing
- **Tag filter** -- AND/OR logic over key-value tags that selects which instances belong to this project
- **Toolset instances** -- the Grafana dashboards, AWS accounts, Datadog orgs, PagerDuty services, etc. that Holmes can query
- **Webhook routing** -- maps incoming webhooks from ADO, PagerDuty, and Salesforce to this project
- **Webhook write-back** -- per-source toggle controlling whether Holmes posts investigation results back to the source system

## Toolset Instances

Instances are the individual integrations Holmes connects to. They live as top-level resources (not nested under a project) and are matched to projects via tags.

Each instance has:

- **Type** -- the base toolset identifier (e.g., `grafana/dashboards`, `aws_api`, `pagerduty`, `ado`, `atlassian`, `datadog/metrics`)
- **Name** -- unique identifier across all instances (e.g., `grafana-logistics`, `aws-pos-prod`)
- **Tags** -- key-value pairs used for project filtering (e.g., `{"project": "logistics", "env": "prod"}`)
- **Config** -- toolset-specific settings like API URL and credentials reference
- **secret_arn** (optional) -- AWS Secrets Manager ARN for per-instance credentials, fetched at runtime by the pod
- **aws_accounts** (optional) -- for AWS instances, restricts which cross-account profiles this instance can query
- **aws_regions** (optional) -- for AWS instances, restricts which regions are queried

## Tag Filtering

Tags connect instances to projects. When Holmes resolves which instances a project can use, it applies the project's tag filter against each instance's tags.

**Rules:**

- An instance with **empty tags** (`{}`) is **global** -- it is always included in every project
- A project with **no tag filter** (null) only gets global instances
- **AND logic** -- all filter key-value pairs must match the instance's tags
- **OR logic** -- at least one filter key-value pair must match

**Example:**

Project "Logistics Cloud" has tag filter: `{"logic": "AND", "tags": {"project": "logistics"}}`

- Instance with tags `{"project": "logistics", "env": "prod"}` -- matches (included)
- Instance with tags `{"project": "pos"}` -- does not match (excluded)
- Instance with tags `{}` -- always included (global)

## Webhook Routing

Incoming webhooks from external systems are routed to the correct project based on identifiers extracted from the webhook payload:

- **ADO** -- the Azure DevOps team project name (`System.TeamProject`) maps to a Holmes project
- **PagerDuty** -- the PagerDuty service ID maps to a Holmes project
- **Salesforce** -- the Salesforce account name maps to a Holmes project

Each project's `webhook_routing` field lists the identifiers it should receive. If no project matches an incoming webhook, Holmes falls back to using only global (untagged) instances.

## Adding a Project

Projects are managed through the Holmes UI at **Settings > Projects**:

1. Create a new project with a name and description
2. Set a tag filter to control which instances are included (or leave empty for global-only)
3. Configure webhook routing if the project should receive ADO, PagerDuty, or Salesforce webhooks
4. Assign users to the project with `project-admin` or `read-only` roles

## Adding an Instance

Instances are also managed through the UI at **Settings > Instances**:

1. Choose the toolset type (Grafana, AWS, Datadog, PagerDuty, etc.)
2. Give it a unique name
3. Set tags so it gets picked up by the right project(s)
4. Configure the connection (API URL, credentials)
5. For AWS instances, select which cross-account profiles and regions to expose

## Example: Multi-Project Setup

A company with two product lines could set up:

**Instances:**

| Name | Type | Tags |
|---|---|---|
| `grafana-shared` | `grafana/dashboards` | `{}` (global) |
| `aws-logistics-prod` | `aws_api` | `{"project": "logistics", "env": "prod"}` |
| `aws-logistics-dev` | `aws_api` | `{"project": "logistics", "env": "dev"}` |
| `aws-pos-prod` | `aws_api` | `{"project": "pos", "env": "prod"}` |
| `dd-pos` | `datadog/metrics` | `{"project": "pos"}` |

**Projects:**

| Project | Tag Filter | Resulting Instances |
|---|---|---|
| Logistics | `AND: project=logistics` | `grafana-shared`, `aws-logistics-prod`, `aws-logistics-dev` |
| POS | `AND: project=pos` | `grafana-shared`, `aws-pos-prod`, `dd-pos` |

Both projects get `grafana-shared` because it has empty tags (global). Each project only sees the AWS accounts and Datadog orgs tagged for its product line.
