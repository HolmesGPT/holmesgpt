from typing import Optional

from pydantic import BaseModel, ConfigDict
from strenum import StrEnum


class IssueStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


# TODO: look at finding in Robusta
class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    # Identifier for the issue - source + issue_id should be unique
    id: str

    # Name of the issue - not necessarily unique
    name: str

    # Source of the issue - e.g. jira
    source_type: str

    # Identifier for the instance of the source - e.g. Jira project key
    source_instance_id: str

    # Link to the issue, when available
    url: Optional[str] = None

    # Raw object from the source - e.g. a dict from the source's API
    raw: Optional[dict] = None

    # these fields are all optional and used for visual presentation of the issue
    # there may not be a 1:1 mapping between source fields and these fields, which is OK
    # e.g. jira issues can have arbitrary statuses like 'closed' and 'resolved' whereas for presentation sake
    # we want to classify as open/closed so we can color the issue red/green
    # if these fields are not present, an LLM  may be used to guess them
    presentation_status: Optional[IssueStatus] = None

    # Markdown with key metadata about the issue. Suggested format is several lines each styled as "*X*: Y" and separated by \n
    presentation_key_metadata: Optional[str] = None

    # Markdown with all metadata about the issue. Suggested to format this with presentation_utils.dict_to_markdown
    presentation_all_metadata: Optional[str] = None

    # title: Optional[str] = None                   # Short title or summary of the issue
    description: Optional[str] = None  # Detailed description of the issue
    # status: Optional[str] = None                  # Current status (e.g., 'open', 'closed', 'resolved')
    # group_id: Optional[str] = None                # Grouping ID from the source (when relevant)
    # priority: Optional[str] = None                # Priority level of the issue (e.g., 'high', 'medium', 'low')
    # created_at: Optional[datetime] = None         # Timestamp of when the issue was created
    # updated_at: Optional[datetime] = None         # Timestamp of when the issue was last updated
    # metadata: Optional[dict] = None               # All additional metadata from the source (can be hierchical - e.g. dicts in dicts
