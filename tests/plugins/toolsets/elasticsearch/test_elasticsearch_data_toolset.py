"""Tests for ElasticsearchDataToolset's llm_instructions."""

from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchDataToolset,
)


class TestElasticsearchDataToolsetInit:
    def test_instructions_loaded(self):
        toolset = ElasticsearchDataToolset()
        assert toolset.llm_instructions
        assert "elasticsearch_search" in toolset.llm_instructions
