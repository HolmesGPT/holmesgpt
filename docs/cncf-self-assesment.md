
# General Technical Review - HolmesGPT / Sandbox

- **Project:** HolmesGPT
- **Project Version:** 0.11.5
- **Website:** https://github.com/robusta-dev/holmesgpt
- **Date Updated:** 2025-07-16
- **Template Version:** v1.0
- **Description:** HolmesGPT is an AI agent that automates cloud-native troubleshooting, bridging knowledge gaps by investigating alerts, executing runbooks, and correlating observability data in cloud-native platforms.

## Day 0 - Planning Phase

### Scope
**Describe the roadmap process, how scope is determined for mid to long term features, as well as how the roadmap maps back to current contributions and maintainer ladder?**

HolmesGPT follows an open and community-driven roadmap process. The roadmap is maintained publicly (via GitHub Projects and issues) and is shaped by a combination of the following inputs:

* Community feedback from users and contributors, gathered through GitHub issues, Slack, and regular discussions
* Strategic alignment with the project’s long-term mission: enabling AI-powered root cause analysis in cloud-native environments
* Technical priorities based on gaps surfaced during usage or contributor pain points
* Upstream integration plans with core CNCF projects like Prometheus, Kubernetes, and OpenTelemetry

Mid to long-term scope is defined quarterly and iterated collaboratively among maintainers

**Describe the target persona or user(s) for the project?**
DevOps, SREs, and platform engineers

**Explain the primary use case for the project. What additional use cases are supported by the project?**

Two primary use cases:
1. Root-cause analysis of alerts (i.e. Prometheus alerts)
2. Troubleshooting problems (via free-text questions) in cloud-native environments

In both cases, the analysis can be 100% autonomous, or driven by predefined runbooks.

**Explain which use cases have been identified as unsupported by the project.**
General purpose AI agent use cases (outside of troubleshooting and RCA), coding agents, and security use cases.

**Describe the intended types of organizations who would benefit from adopting this project. (i.e. financial services, any software manufacturer, organizations providing platform engineering services)?**

End users running cloud-native services at scale, especially in larger environments with many microservices and potential for complex cascading failures.

**Please describe any completed end user research and link to any reports.**
N/A

### Usability

**How should the target personas interact with your project?**
The most common entry point is via a cli tool that is run on-demand to troubleshoot a problem or an alert.

**Describe the user experience (UX) and user interface (UI) of the project.**
The open source includes a CLI tool with an interactive console and /slash commands. Several vendors have built graphical user interfaces on top of that.

**Describe how this project integrates with other projects in a production environment.**
The project exposes an HTTP API and a Helm chart for running the HTTP server in Kubernetes clusters.

### Design

**Explain the design principles and best practices the project is following.**
* Human-in-the-loop: users are able to interact with and guide HolmesGPT investigations
* Safety-first - The agent is restricted by default and only allowed to run safe commands.
* Interoperable – works seamlessly with existing observability stacks
* Kubernetes-native: Works with Prometheus, Loki, and other CNCF stack components.
* Extensible: Modular plugin system for adding new data sources, including external MCP servers

**Outline or link to the project’s architecture requirements? Describe how they differ for Proof of Concept, Development, Test and Production environments, as applicable.**
Lightweight, can run as a standalone local CLI or in-cluster as an HTTP server. Typical during POC users start with the local CLI and when rolling into production use a more advanced setup.

**Define any specific service dependencies the project relies on in the cluster.**
No relevant.

**Describe how the project implements Identity and Access Management.**
HolmesGPT runs with user-provided credentials (e.g. service account) and respects whichever permissions were given to it.

**Describe how the project has addressed sovereignty.**
HolmesGPT runs fully within the user’s infrastructure. All data—logs, metrics, traces, and AI-generated insights—remains under user control. There’s no dependency on external SaaS or third-party APIs unless explicitly configured. This ensures data privacy, compliance, and operational sovereignty.

Regarding data sent to the LLM, here too users have the choice of providing their own LLM or using a trusted cloud provider of their choice.

**Describe any compliance requirements addressed by the project.**
N/A

**Describe the project’s High Availability requirements.**
Each request to HolmesGPT is stateless, so it's possible to run multiple instances.

**Describe the project’s resource requirements, including CPU, Network and Memory.**
Minimal, similar to any standard Python application running in a Kubernetes cluster. We recommend some defaults in the Helm chart, but this can be customized by the user.

**Describe the project’s storage requirements, including its use of ephemeral and/or persistent storage.**
N/A

**Please outline the project’s API Design**
The project itself exposes a REST API, following standard conventions. We strive to maintain backwards compatibility, and to add new endpoints when changing something instead of breaking an existing endpoint.

It will perform HTTP calls to collect data when investigating problems - the exact calls depend on which data sources the user enabled.

We bump the major release number only on breaking changes. Minor releases are done about monthly, when there are substantial new features. Bug fixes are done as needed with a patch release.

**Describe how the project is installed and initialized, e.g. a minimal install with a few lines of code or does it require more complex integration and configuration?**
Please refer to https://holmesgpt.dev/installation/cli-installation/

**How does an adopter test and validate the installation?**
Please refer to https://holmesgpt.dev/walkthrough/

### Security

**Please provide a link to the project’s cloud native [security self assessment](https://tag-security.cncf.io/community/assessments/).**

**Please review the [Cloud Native Security Tenets](https://github.com/cncf/tag-security/blob/main/community/resources/security-whitepaper/secure-defaults-cloud-native-8.md) from TAG Security. How are you satisfying the tenets of cloud native security projects?**
This is extremely relevant for us, given the risk that AI models can hallucinate and thereby that HolmesGPT could run malicious commands. To mitigate this, default access is read-only and non-mutating and limited to a pre-approved list of safe commands and integrations.

**Describe how each of the cloud native principles apply to your project.**
* Make security a design requirement - see above.
* Applying secure configuration has the best user experience - also covered above
* Selecting insecure configuration is a conscious decision -  Users must make a conscious and concerted effort to add insecure toolsets (data sources) to HolmesGPT - it cannot be done accidentally.
* Transition from insecure to secure state is possible - users are free to reduce the permissions with which Holmes runs at any point in time and Holmes will identify it and adapt
* Secure defaults are inherited - by default Holmes inherits service roles and permissions from its environment
* Exception lists have first class support - users can add their own toolsets to give Holmes access to additional commands
* Secure defaults protect against pervasive vulnerability exploits - in the case of Holmes, this is equivalent to providing security even when used with malicious/hallucinating LLM which is done as described above
* Security limitations of a system are explainable - Holmes reports permission issues when encountered

**How do you recommend users alter security defaults in order to "loosen" the security of the project? Please link to any documentation the project has written concerning these use cases.**
https://holmesgpt.dev/data-sources/permissions/

**Security Hygiene**
We discuss security implications of features at the design phase, when working on new features. Where warranted there are dedicated discussions around security related aspects. Code reviews function as the final review, but very few issues reach that stage due to thinking about security earlier in the process.

**Explain the least minimal privileges required by the project and reasons for additional privileges.**
Read-only access to the data sources that are relevant to the requested RCA.

**Describe how the project is handling certificate rotation and mitigates any issues with certificates.**
Not relevant.

**Describe how the project is following and implementing [secure software supply chain best practices](https://project.linuxfoundation.org/hubfs/CNCF\_SSCP\_v1.pdf)**
Link is broken, but we strictly review all changes to CI/CD and anything that impacts building the project and distributing it to end users.
