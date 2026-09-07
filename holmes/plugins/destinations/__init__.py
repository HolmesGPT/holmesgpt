from strenum import StrEnum


class DestinationType(StrEnum):
    SLACK = "slack"
    TEAMS = "teams"
    CLI = "cli"
