from strenum import StrEnum


class DestinationType(StrEnum):
    SLACK = "slack"
    CLI = "cli"
    WEBHOOK = "webhook"
