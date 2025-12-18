from holmes.plugins.toolsets.bash.common.bash_command import (
    SimpleBashCommand,
)


class WCCommand(SimpleBashCommand):
    def __init__(self):
        super().__init__(
            name="wc",
            allowed_options=[],
            denied_options=["--files0-from"],
        )
