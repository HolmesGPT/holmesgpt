from holmes.plugins.toolsets.bash.common.bash_command import SimpleBashCommand


class GrepCommand(SimpleBashCommand):
    def __init__(self):
        super().__init__(
            name="grep",
            allowed_options=[],
            denied_options=[],
        )
