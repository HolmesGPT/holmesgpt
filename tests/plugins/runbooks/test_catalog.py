import os

from holmes.plugins.runbooks import (
    DEFAULT_RUNBOOK_SEARCH_PATH,
    get_runbook_by_path,
    load_runbook_catalog,
)


def test_load_runbook_catalog():
    runbooks = load_runbook_catalog()
    assert runbooks is not None
    assert len(runbooks.catalog) > 0
    for runbook in runbooks.catalog:
        assert runbook.description is not None
        assert runbook.link is not None
        runbook_link = get_runbook_by_path(runbook.link, [DEFAULT_RUNBOOK_SEARCH_PATH])
        # assert file path exists
        assert os.path.exists(
            runbook_link
        ), f"Runbook link {runbook.link} does not exist at {runbook_link}"


def test_load_runbook_catalog_with_custom_catalog(tmp_path):
    """Test loading runbook catalog with a custom catalog file."""
    import json

    # Create a custom catalog file
    custom_catalog_file = tmp_path / "custom_catalog.json"
    custom_catalog_data = {
        "catalog": [
            {
                "id": "custom-test-runbook",
                "update_date": "2023-01-01",
                "description": "Custom test runbook",
                "link": "custom_test.md",
            }
        ]
    }
    custom_catalog_file.write_text(json.dumps(custom_catalog_data))

    # Load catalog with custom catalog path
    runbooks = load_runbook_catalog(custom_catalog_paths=[custom_catalog_file])

    assert runbooks is not None
    # Should have both builtin and custom runbooks
    assert len(runbooks.catalog) > 1

    # Check that custom runbook is in the catalog
    custom_runbook_links = [r.link for r in runbooks.catalog]
    assert "custom_test.md" in custom_runbook_links


def test_load_runbook_catalog_with_multiple_custom_catalogs(tmp_path):
    """Test loading runbook catalog with multiple custom catalog files."""
    import json

    # Create multiple custom catalog files
    custom_catalogs = []
    for i in range(2):
        custom_catalog_file = tmp_path / f"custom_catalog_{i}.json"
        custom_catalog_data = {
            "catalog": [
                {
                    "id": f"custom-runbook-{i}",
                    "update_date": "2023-01-01",
                    "description": f"Custom runbook {i}",
                    "link": f"custom_{i}.md",
                }
            ]
        }
        custom_catalog_file.write_text(json.dumps(custom_catalog_data))
        custom_catalogs.append(custom_catalog_file)

    # Load catalog with multiple custom catalog paths
    runbooks = load_runbook_catalog(custom_catalog_paths=custom_catalogs)

    assert runbooks is not None
    # Should have builtin runbooks plus 2 custom runbooks
    custom_runbook_links = [r.link for r in runbooks.catalog]
    assert "custom_0.md" in custom_runbook_links
    assert "custom_1.md" in custom_runbook_links


def test_load_runbook_catalog_with_nonexistent_custom_catalog(tmp_path):
    """Test that nonexistent custom catalog files are handled gracefully."""
    nonexistent_file = tmp_path / "nonexistent.json"

    # Should not raise an exception
    runbooks = load_runbook_catalog(custom_catalog_paths=[nonexistent_file])

    # Should still have builtin runbooks
    assert runbooks is not None
    assert len(runbooks.catalog) > 0


def test_load_runbook_catalog_with_invalid_json(tmp_path):
    """Test that invalid JSON in custom catalog is handled gracefully."""

    invalid_catalog_file = tmp_path / "invalid.json"
    invalid_catalog_file.write_text("{ invalid json content")

    # Should not raise an exception
    runbooks = load_runbook_catalog(custom_catalog_paths=[invalid_catalog_file])

    # Should still have builtin runbooks
    assert runbooks is not None
    assert len(runbooks.catalog) > 0


def test_load_runbook_catalog_with_empty_custom_catalog(tmp_path):
    """Test loading with an empty custom catalog."""
    import json

    empty_catalog_file = tmp_path / "empty.json"
    empty_catalog_data = {"catalog": []}
    empty_catalog_file.write_text(json.dumps(empty_catalog_data))

    runbooks = load_runbook_catalog(custom_catalog_paths=[empty_catalog_file])

    # Should still have builtin runbooks
    assert runbooks is not None
    assert len(runbooks.catalog) > 0
