# Contributing

## Before you get started

### Code of Conduct

Please make sure to read and observe our [Governance](./GOVERNANCE.md) docs.

### Install requirements
- Python `3.11`
  - poetry `1.8.4` & up
- A LLM API key is required to use and test HolmesGPT
  - OpenAI's `gpt4-o` is recommended.
  - For details see [Getting an API Key](https://holmesgpt.dev/ai-providers/).

## Reporting bugs

We encourage those interested to contribute code and also appreciate when issues are reported.

- Create a new issue and label is as `bug`
- Clearly state how to reproduce the bug:
  - Which LLM you've used
  - Which steps are required to reproduce
    - As LLMs answers may differ between runs - Does it always reproduce, or occasionally?


## Contributing Code

- Fork the repository and clone it locally.
- Create a new branch and make your changes
- Add or update tests to ensure your changes are covered.
- Run `pytest` to verify all tests pass.
- Keep pull requests small and focused. if you have multiple changes, open a PR for each.
- Create a pull request back to the upstream repository.
- Wait for a review and address any comments
- Follow the guidelines in our [Governance](./GOVERNANCE.md) docs regarding code contributions
