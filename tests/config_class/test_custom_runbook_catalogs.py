import json

from holmes.config import Config


def test_config_custom_runbook_catalogs_from_file(tmp_path):
    """Test that custom_runbook_catalogs is loaded from config file."""
    # Create a custom catalog file
    custom_catalog_file = tmp_path / "catalog.json"
    catalog_data = {
        "catalog": [
            {
                "id": "test-runbook",
                "update_date": "2023-01-01",
                "description": "Test runbook",
                "link": "test.md",
            }
        ]
    }
    custom_catalog_file.write_text(json.dumps(catalog_data))

    # Create a config file
    config_file = tmp_path / "config.yaml"
    config_content = f"""
model: "gpt-4"
custom_runbook_catalogs:
  - {custom_catalog_file}
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    assert config.custom_runbook_catalogs is not None
    assert len(config.custom_runbook_catalogs) == 1
    assert str(config.custom_runbook_catalogs[0]) == str(custom_catalog_file)


def test_config_custom_runbook_catalogs_multiple(tmp_path):
    """Test that multiple custom_runbook_catalogs are loaded from config file."""
    # Create multiple custom catalog files
    catalog_files = []
    for i in range(2):
        custom_catalog_file = tmp_path / f"catalog_{i}.json"
        catalog_data = {
            "catalog": [
                {
                    "id": f"test-runbook-{i}",
                    "update_date": "2023-01-01",
                    "description": f"Test runbook {i}",
                    "link": f"test_{i}.md",
                }
            ]
        }
        custom_catalog_file.write_text(json.dumps(catalog_data))
        catalog_files.append(custom_catalog_file)

    # Create a config file
    config_file = tmp_path / "config.yaml"
    config_content = f"""
model: "gpt-4"
custom_runbook_catalogs:
  - {catalog_files[0]}
  - {catalog_files[1]}
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    assert config.custom_runbook_catalogs is not None
    assert len(config.custom_runbook_catalogs) == 2
    assert str(config.custom_runbook_catalogs[0]) == str(catalog_files[0])
    assert str(config.custom_runbook_catalogs[1]) == str(catalog_files[1])


def test_config_custom_runbook_catalogs_empty(tmp_path):
    """Test that empty custom_runbook_catalogs list is handled correctly."""
    # Create a config file with empty list
    config_file = tmp_path / "config.yaml"
    config_content = """
model: "gpt-4"
custom_runbook_catalogs: []
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    assert config.custom_runbook_catalogs is not None
    assert len(config.custom_runbook_catalogs) == 0


def test_config_custom_runbook_catalogs_not_specified(tmp_path):
    """Test that custom_runbook_catalogs defaults to empty list when not specified."""
    # Create a config file without custom_runbook_catalogs
    config_file = tmp_path / "config.yaml"
    config_content = """
model: "gpt-4"
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    assert config.custom_runbook_catalogs is not None
    assert len(config.custom_runbook_catalogs) == 0


def test_config_custom_runbook_catalogs_passed_to_toolset_manager(tmp_path):
    """Test that custom_runbook_catalogs is passed to ToolsetManager."""
    # Create a custom catalog file
    custom_catalog_file = tmp_path / "catalog.json"
    catalog_data = {
        "catalog": [
            {
                "id": "test-runbook",
                "update_date": "2023-01-01",
                "description": "Test runbook",
                "link": "test.md",
            }
        ]
    }
    custom_catalog_file.write_text(json.dumps(catalog_data))

    # Create a config file
    config_file = tmp_path / "config.yaml"
    config_content = f"""
model: "gpt-4"
custom_runbook_catalogs:
  - {custom_catalog_file}
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    # Access toolset_manager property
    toolset_manager = config.toolset_manager

    # Verify that custom_runbook_catalogs is passed to ToolsetManager
    assert toolset_manager.custom_runbook_catalogs is not None
    assert len(toolset_manager.custom_runbook_catalogs) == 1
    assert str(toolset_manager.custom_runbook_catalogs[0]) == str(custom_catalog_file)


def test_config_get_runbook_catalog_with_custom_catalogs(tmp_path):
    """Test that Config.get_runbook_catalog() uses custom_runbook_catalogs."""
    # Create a custom catalog file
    custom_catalog_file = tmp_path / "catalog.json"
    catalog_data = {
        "catalog": [
            {
                "id": "test-runbook",
                "update_date": "2023-01-01",
                "description": "Test custom runbook",
                "link": "test_custom.md",
            }
        ]
    }
    custom_catalog_file.write_text(json.dumps(catalog_data))

    # Create a config file
    config_file = tmp_path / "config.yaml"
    config_content = f"""
model: "gpt-4"
custom_runbook_catalogs:
  - {custom_catalog_file}
"""
    config_file.write_text(config_content)

    # Load config
    config = Config.load_from_file(config_file)

    # Get runbook catalog
    runbook_catalog = config.get_runbook_catalog()

    assert runbook_catalog is not None
    # Should have both builtin and custom runbooks
    runbook_links = [r.link for r in runbook_catalog.catalog]
    assert "test_custom.md" in runbook_links
