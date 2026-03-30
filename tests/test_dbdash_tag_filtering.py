from holmes.plugins.toolsets.dbdash.common import filter_instances_by_tags


class TestFilterInstancesByTags:
    def test_no_tags_configured_returns_all(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = []
        result = filter_instances_by_tags(instances, instance_tags, configured_tags=None)
        assert len(result) == 2

    def test_single_tag_filters_correctly(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01"},
            {"InstanceID": 3, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 2, "TagName": "project", "TagValue": "staging"},
            {"InstanceID": 3, "TagName": "project", "TagValue": "payments"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 2
        assert result[0]["InstanceID"] == 1
        assert result[1]["InstanceID"] == 3

    def test_multiple_tags_require_all_match(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 1, "TagName": "environment", "TagValue": "production"},
            {"InstanceID": 2, "TagName": "project", "TagValue": "payments"},
            {"InstanceID": 2, "TagName": "environment", "TagValue": "staging"},
        ]
        result = filter_instances_by_tags(
            instances,
            instance_tags,
            configured_tags={"project": "payments", "environment": "production"},
        )
        assert len(result) == 1
        assert result[0]["InstanceID"] == 1

    def test_no_matching_instances_returns_empty(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "logistics"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 0

    def test_instance_without_tags_excluded_when_tags_configured(self):
        instances = [
            {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            {"InstanceID": 2, "InstanceDisplayName": "untagged-sql"},
        ]
        instance_tags = [
            {"InstanceID": 1, "TagName": "project", "TagValue": "payments"},
        ]
        result = filter_instances_by_tags(
            instances, instance_tags, configured_tags={"project": "payments"}
        )
        assert len(result) == 1
        assert result[0]["InstanceID"] == 1
