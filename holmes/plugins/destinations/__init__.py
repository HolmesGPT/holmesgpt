from strenum import StrEnum


class DestinationType(StrEnum):
    SLACK = "slack"
    MATTERMOST = "mattermost"
    CLI = "cli"
