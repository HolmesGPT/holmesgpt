You are an expert in automated diagnostics and runbook creation for an AI-driven troubleshooting agents. I will provide you with one or more issue descriptions or test scenarios.

Your task is to generate a strictly executable runbook for AI Agent to follow. The runbook should be machine-readable but human-understandable, and must include the following sections:

# Runbook Content Structure

## 1. Goal
- **Primary Objective:** Clearly define the specific category of issues this runbook addresses (e.g., "diagnose network connectivity problems", "troubleshoot pod startup failures", "investigate performance degradation").
- **Scope:** Specify the environment, technology stack, or system components covered by this runbook.
- **Agent Mandate:** Explicitly state that the AI agent must follow the workflow steps sequentially and systematically without deviation to ensure consistent, thorough troubleshooting.
- **Expected Outcome:** Define what successful completion of this runbook should achieve (root cause identification, issue resolution, or escalation criteria).

## 2. Workflow for [Issue Category] Diagnosis
- Provide numbered, sequential steps the AI agent must execute in order.
- Each step should specify:
  - **Action:** Describe the diagnostic function conceptually (e.g., "retrieve container logs from specified pod", "check service connectivity between components", "examine resource utilization metrics")
  - **Function Description:** Explain what the function should accomplish rather than naming specific tools (e.g., "query the cluster to list all pods in a namespace and their current status" instead of "kubectl_get_pods()")
  - **Parameters:** What data/arguments to pass to the function (namespace, pod name, time range, etc.)
  - **Expected Output:** What information to gather from the result (status codes, error messages, metrics, configurations)
  - **Success/Failure Criteria:** How to interpret the output and what indicates normal vs. problematic conditions
- Use conditional logic (IF/ELSE) when branching is required based on findings.
- Describe functions generically so they can be mapped to available tools (e.g., "execute a command to test network connectivity" rather than "ping_host()")
- Include verification steps to confirm each diagnostic action was successful.

## 3. Synthesize Findings
- **Data Correlation:** Describe how the AI agent should combine outputs from multiple workflow steps.
- **Pattern Recognition:** Specify what patterns, error messages, or metrics indicate specific root causes.
- **Prioritization Logic:** Provide criteria for ranking potential causes by likelihood or severity.
- **Evidence Requirements:** Define what evidence is needed to confidently identify each potential root cause.
- **Example Scenarios:** Include sample synthesis statements showing how findings should be summarized.

## 4. Recommended Remediation Steps
- **Immediate Actions:** List temporary workarounds or urgent fixes for critical issues.
- **Permanent Solutions:** Provide step-by-step permanent remediation procedures.
- **Verification Steps:** Define how to confirm each remediation action was successful.
- **Documentation References:** Include links to official documentation, best practices, or vendor guidance.
- **Escalation Criteria:** Specify when and how to escalate if remediation steps fail.
- **Post-Remediation Monitoring:** Describe what to monitor to prevent recurrence.

# File Organization Guidelines

## Folder Structure
*Category folders are used to distinguish and categorize different runbooks based on their focus area or technology domain. Each runbook must be placed into a specific category folder under `holmes/plugins/runbooks/` for better organization and discoverability. Create a new category folder if your runbook doesn't fit into existing categories.*

## File Naming
*Use consistent naming conventions for runbook files:*

- Use descriptive, lowercase names with hyphens: `dns-resolution-troubleshooting.md`
- Include the issue type or technology: `redis-connection-issues.md`
- Avoid generic names like `troubleshooting.md` or `debug.md`

### Catalog Registration
After creating your runbook, you must add an entry to `catalog.json` in the runbooks directory to make it discoverable by AI agents.

**Steps to add a new catalog entry (actual schema):**

1. **Open** `holmes/plugins/runbooks/catalog.json`
2. **Add your entry** to the `catalog` array with the following fields:
   ```json
   {
     "id": "unique-id-or-filename.md",          // required, unique identifier
     "update_date": "2025-12-30",               // required, ISO date (YYYY-MM-DD)
     "description": "What issues this runbook covers", // required, short summary
     "link": "category-folder/runbook-file.md"  // required, path relative to runbooks root
   }
   ```

3. **Ensure proper JSON formatting** - add a comma after the previous entry if needed.
4. **Validate the JSON** is properly formatted before committing.

**Field Guidelines:**
- `id` (required): Unique identifier for the runbook (often matches the filename).
- `update_date` (required): ISO 8601 date string (`YYYY-MM-DD`) representing last update.
- `description` (required): Clear, concise description of what the runbook addresses.
- `link` (required): Relative path to the markdown file, including the category folder (e.g., `"networking/dns_troubleshooting_instructions.md"`).

Example catalog entry:
```json
{
  "id": "tempo-trace-comparison.md",
  "update_date": "2025-12-30",
  "description": "Use when investigating reported latency or tail-latency spikes; compares fast, typical, and slow Tempo traces to isolate the bottleneck",
  "link": "tracing/tempo-trace-comparison.md"
}
```
