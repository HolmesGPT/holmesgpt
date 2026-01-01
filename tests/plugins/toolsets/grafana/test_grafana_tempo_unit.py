from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    GrafanaTempoToolset,
)


def test_build_k8s_filters():
    """Test the shared build_k8s_filters utility method."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config

    # Test exact match filters
    params = {
        "service_name": "my-service",
        "pod_name": "my-pod",
        "namespace_name": "my-namespace",
        "deployment_name": "my-deployment",
        "node_name": "my-node",
    }

    exact_filters = toolset.build_k8s_filters(params, use_exact_match=True)
    assert len(exact_filters) == 5
    assert 'resource.service.name="my-service"' in exact_filters
    assert 'resource.k8s.pod.name="my-pod"' in exact_filters
    assert 'resource.k8s.namespace.name="my-namespace"' in exact_filters
    assert 'resource.k8s.deployment.name="my-deployment"' in exact_filters
    assert 'resource.k8s.node.name="my-node"' in exact_filters

    # Test regex match filters
    regex_filters = toolset.build_k8s_filters(params, use_exact_match=False)
    assert len(regex_filters) == 5
    assert 'resource.service.name=~".*my-service.*"' in regex_filters
    assert 'resource.k8s.pod.name=~".*my-pod.*"' in regex_filters
    assert 'resource.k8s.namespace.name=~".*my-namespace.*"' in regex_filters
    assert 'resource.k8s.deployment.name=~".*my-deployment.*"' in regex_filters
    assert 'resource.k8s.node.name=~".*my-node.*"' in regex_filters


def test_build_k8s_filters_with_special_characters():
    """Test that special regex characters are properly escaped."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config

    # Test with special regex characters
    params = {
        "service_name": "test.service[1]",
        "pod_name": "pod-with(parens)",
        "namespace_name": "namespace.*",
        "deployment_name": "deploy+test",
        "node_name": "node^name$",
    }

    # Test regex match filters - all special chars should be escaped
    regex_filters = toolset.build_k8s_filters(params, use_exact_match=False)
    assert len(regex_filters) == 5
    assert 'resource.service.name=~".*test.service[1].*"' in regex_filters
    assert 'resource.k8s.pod.name=~".*pod-with(parens).*"' in regex_filters
    assert 'resource.k8s.namespace.name=~".*namespace.*.*"' in regex_filters
    assert 'resource.k8s.deployment.name=~".*deploy+test.*"' in regex_filters
    assert 'resource.k8s.node.name=~".*node^name$.*"' in regex_filters

    # Test exact match with quotes
    params_with_quotes = {
        "service_name": 'service"with"quotes',
    }
    exact_filters = toolset.build_k8s_filters(params_with_quotes, use_exact_match=True)
    assert 'resource.service.name="service\\"with\\"quotes"' in exact_filters
