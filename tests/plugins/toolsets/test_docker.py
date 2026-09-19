from pathlib import Path

from holmes.plugins.toolsets import load_toolsets_from_file


def test_docker_events_is_a_bounded_snapshot():
    toolsets_path = (
        Path(__file__).parents[3] / "holmes" / "plugins" / "toolsets" / "docker.yaml"
    )
    docker_toolset = load_toolsets_from_file(str(toolsets_path))[0]
    docker_events = next(
        tool for tool in docker_toolset.tools if tool.name == "docker_events"
    )

    assert docker_events.command == "docker events --since 10m --until 1s"
