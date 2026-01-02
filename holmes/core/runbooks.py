from typing import List

from holmes.core.issue import Issue
from holmes.plugins.runbooks import Runbook


# TODO: our default prompt has a lot of kubernetes specific stuff - see if we can get that into the runbook
class RunbookManager:
    def __init__(self, runbooks: List[Runbook]):
        self.runbooks = runbooks

    def get_instructions_for_issue(self, issue: Issue) -> List[str]:
        instructions = []
        for runbook in self.runbooks:
            if runbook.match.issue_id and not runbook.match.issue_id.match(issue.id):
                continue
            if runbook.match.issue_name and not runbook.match.issue_name.match(
                issue.name
            ):
                continue
            if runbook.match.source and not runbook.match.source.match(
                issue.source_type
            ):
                continue
            instructions.append(runbook.instructions)

        return instructions
