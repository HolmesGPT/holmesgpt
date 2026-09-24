import markdown
import pytest

from docs.custom_fences import MultiInstanceFenceError, multi_instance_fence_format


def render(block: str) -> str:
    """Render a page holding one block, with the fence registered as in mkdocs.yml."""
    return markdown.markdown(
        f"```multi-instance\n{block}```\n",
        extensions=["pymdownx.superfences"],
        extension_configs={
            "pymdownx.superfences": {
                "custom_fences": [
                    {
                        "name": "multi-instance",
                        "class": "multi-instance",
                        "format": multi_instance_fence_format,
                    }
                ]
            }
        },
    )


def test_renders_the_config_under_each_instance():
    out = render(
        "toolset: prometheus/metrics\n"
        "name: Prometheus\n"
        "config: |\n"
        "  prometheus_url: http://prometheus:9090\n"
    )
    assert "The Prometheus toolset can connect to more than one" in out
    assert out.count("prometheus_url: http://prometheus:9090") == 2
    assert "prometheus_metrics_list_instances" in out


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("toolset: prometheus/metrics\n", id="missing-config"),
        pytest.param("config: |\n  prometheus_url: x\n", id="missing-toolset"),
        pytest.param("toolset: [prometheus\n", id="invalid-yaml"),
        pytest.param("- prometheus/metrics\n", id="not-a-mapping"),
    ],
)
def test_bad_block_fails_the_build(block):
    with pytest.raises(MultiInstanceFenceError):
        render(block)
