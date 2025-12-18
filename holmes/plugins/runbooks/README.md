# Runbooks

Runbooks folder contains operational runbooks for the HolmesGPT project. Runbooks provide step-by-step instructions for common tasks, troubleshooting, and maintenance procedures related to the plugins in this directory.

## Purpose

- Standardize operational processes
- Enable quick onboarding for new team members
- Reduce downtime by providing clear troubleshooting steps

## Structure

### Structured Runbook

Structured runbooks are designed for specific issues when conditions like issue name, id or source match, the corresponding instructions will be returned for investigation.
For example, the investigation in [kube-prometheus-stack.yaml](kube-prometheus-stack.yaml) will be returned when the issue to be investigated match either KubeSchedulerDown or KubeControllerManagerDown.
This runbook is mainly used for `holmes investigate`

### Catalog

Catalog specified in [catalog.json](catalog.json) contains a collection of runbooks written in markdown.
During runtime, LLM will compare the runbook description with the user question and return the most matched runbook for investigation. It's possible no runbook is returned for no match.

## Generating Runbooks

To ensure all runbooks follow a consistent format and improve troubleshooting accuracy, contributors should use the standardized [runbook format prompt](runbook-format.prompt.md) when creating new runbooks.

### Using the Runbook Format Prompt

1. **Start with the Template**: Use `prompt.md` as your guide when creating new runbooks
2. **Follow the Structure**: Ensure your runbook includes all required sections:
   - **Goal**: Clear definition of issues addressed and agent mandate
   - **Workflow**: Sequential diagnostic steps with detailed function descriptions
   - **Synthesize Findings**: Logic for combining outputs and identifying root causes
   - **Recommended Remediation Steps**: Both immediate and permanent solutions

### Benefits of Using the Standard Format

- **Consistency**: All runbooks follow the same structure and terminology
- **AI Agent Compatibility**: Ensures runbooks are machine-readable and executable by AI agents
- **Improved Accuracy**: Standardized format reduces ambiguity and improves diagnostic success rates
- **Maintainability**: Easier to update and maintain runbooks across the project

### Example Usage

When creating a runbook for a new issue category (e.g., storage problems, authentication failures), provide the issue description to an LLM along with the prompt template to generate a properly formatted runbook that follows the established patterns.
