# Skills

Skills folder contains operational skills for the HolmesGPT project. Skills provide step-by-step instructions for common tasks, troubleshooting, and maintenance procedures related to the plugins in this directory.

## Purpose

- Standardize operational processes
- Enable quick onboarding for new team members
- Reduce downtime by providing clear troubleshooting steps

## Structure

### Structured Skill

Structured skills are designed for specific issues when conditions like issue name, id or source match, the corresponding instructions will be returned for investigation.
For example, the investigation in [kube-prometheus-stack.yaml](kube-prometheus-stack.yaml) will be returned when the issue to be investigated match either KubeSchedulerDown or KubeControllerManagerDown.
This skill is mainly used for `holmes investigate`

### Skills Directory

Each skill is a directory containing a `SKILL.md` file with YAML frontmatter (name, description) and markdown body (the instructions).
Skills are placed under `holmes/plugins/skills/builtin/` for builtin skills, or in any directory configured via `custom_skill_paths`.
During runtime, the LLM will compare the skill description with the user question and fetch the most matched skill for investigation. It's possible no skill is fetched for no match.

## Generating Skills

To ensure all skills follow a consistent format and improve troubleshooting accuracy, contributors should use the standardized [skill format prompt](skill-format.prompt.md) when creating new skills.

### Using the Skill Format Prompt

1. **Start with the Template**: Use `prompt.md` as your guide when creating new skills
2. **Follow the Structure**: Ensure your skill includes all required sections:
   - **Goal**: Clear definition of issues addressed and agent mandate
   - **Workflow**: Sequential diagnostic steps with detailed function descriptions
   - **Synthesize Findings**: Logic for combining outputs and identifying root causes
   - **Recommended Remediation Steps**: Both immediate and permanent solutions

### Benefits of Using the Standard Format

- **Consistency**: All skills follow the same structure and terminology
- **AI Agent Compatibility**: Ensures skills are machine-readable and executable by AI agents
- **Improved Accuracy**: Standardized format reduces ambiguity and improves diagnostic success rates
- **Maintainability**: Easier to update and maintain skills across the project

### Example Usage

When creating a skill for a new issue category (e.g., storage problems, authentication failures), provide the issue description to an LLM along with the prompt template to generate a properly formatted skill that follows the established patterns.
