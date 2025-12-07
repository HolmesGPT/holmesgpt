# Example Custom Application Troubleshooting

## Goal
Your primary goal is to diagnose and troubleshoot issues in the custom application by following the workflow below.

* Use available tools to gather information about the application state.
* Clearly present key findings from tool outputs in your analysis.
* Follow the troubleshooting workflow step-by-step rather than providing general advice.

## Workflow

1. **Check Application Health**
   * Verify that the application pods are running
   * Check for restarts or crashes
   * Review resource usage (CPU, memory)

2. **Examine Application Logs**
   * Retrieve recent logs from the application pods
   * Look for error messages or warnings
   * Check for stack traces or exception messages

3. **Verify Dependencies**
   * Check if dependent services are accessible
   * Verify database connectivity
   * Check external API endpoints if applicable

4. **Review Configuration**
   * Validate environment variables
   * Check mounted ConfigMaps and Secrets
   * Verify service discovery settings

## Synthesize Findings

Based on the outputs from the above steps, describe the issue clearly. For example:
* "Application pods are in CrashLoopBackOff due to failed database connection. Connection string is missing from environment variables."
* "High memory usage detected (95% of limit). Application logs show memory leak in background processing task."

## Recommended Remediation Steps

* **For configuration issues**: Review and update ConfigMaps/Secrets with correct values
* **For resource issues**: Adjust resource limits or scale the application
* **For dependency issues**: Ensure dependent services are running and accessible
* **For code issues**: Review application logs and fix bugs in the application code

Refer to your organization's internal documentation for specific deployment and configuration procedures.
