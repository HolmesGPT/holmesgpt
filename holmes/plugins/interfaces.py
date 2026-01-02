from typing import Iterable, List

from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult


# Sources must implement this
class SourcePlugin:
    def fetch_issues(self) -> List[Issue]:
        raise NotImplementedError()

    def fetch_issue(self, id: str) -> Issue:
        raise NotImplementedError()

    # optional
    def stream_issues(self) -> Iterable[Issue]:
        raise NotImplementedError()

    # optional
    def write_back_result(self, issue_id: str, result_data: LLMResult) -> None:
        raise NotImplementedError()


# Destinations must implement this
class DestinationPlugin:
    def send_issue(self, issue: Issue, result: LLMResult):
        raise NotImplementedError()

    # def send_grouped_issues(self, issues: List[Issue]):
    #    raise NotImplementedError()

    # def group_issues()
    #    raise NotImplementedError()
