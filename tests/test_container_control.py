import pytest
from container_control import _ensure_config_sitemap_structure


def test_ensure_structure_legacy_format():
    data = {
        "Traffic Generator URL": "https://example.com",
        "Rate Limit": 5,
        "sitemap": {"has_auth": False, "paths": []},
    }
    result = _ensure_config_sitemap_structure(data.copy())
    assert "config" in result
    assert result["config"]["Traffic Generator URL"] == "https://example.com"
    assert result["config"]["Rate Limit"] == 5
    assert result["sitemap"] == {"has_auth": False, "paths": []}


def test_ensure_structure_with_config_key():
    data = {
        "config": {"Traffic Generator URL": "https://example.com"},
        "sitemap": {"has_auth": False, "paths": []},
    }
    result = _ensure_config_sitemap_structure(data.copy())
    assert result == data


def test_ensure_structure_sitemap_metadata():
    data = {
        "config": {"Traffic Generator URL": "https://example.com"},
        "sitemap": {
            "id": 1,
            "name": "test",
            "sitemap": {"has_auth": False, "paths": []},
        },
        "Extra": True,
    }
    result = _ensure_config_sitemap_structure(data.copy())
    assert result["config"]["Extra"] is True
    assert result["sitemap"] == {"has_auth": False, "paths": []}
