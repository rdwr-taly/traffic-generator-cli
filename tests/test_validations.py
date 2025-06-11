import pytest
from pydantic import ValidationError

from traffic_generator import (
    ContainerConfig,
    SiteMap,
    PathDefinition,
    Metrics,
    TrafficGenerator,
)


def minimal_config(**overrides):
    base = {
        "Traffic Generator URL": "http://example.com",
        "XFF Header Name": "X-Forwarded-For",
        "Rate Limit": 1,
        "Simulated Users": 1,
        "Minimum Session Length": 1,
        "Maximum Session Length": 1,
    }
    base.update(overrides)
    return ContainerConfig(**base)


def minimal_sitemap(**overrides):
    base = {
        "has_auth": False,
        "paths": [PathDefinition(method="GET", paths=["/"], traffic_type="web")],
    }
    base.update(overrides)
    return SiteMap(**base)


def test_container_config_dns_override_blank():
    cfg = minimal_config(**{"Traffic Generator DNS Override": ""})
    assert cfg.traffic_target_dns_override is None


def test_container_config_invalid_dns():
    with pytest.raises(ValidationError):
        minimal_config(**{"Traffic Generator DNS Override": "not_an_ip"})


def test_sitemap_requires_auth_config():
    with pytest.raises(ValidationError):
        minimal_sitemap(has_auth=True)


def test_sitemap_auth_ignored_when_disabled():
    sm = minimal_sitemap(
        has_auth=False, auth={"auth_type": "", "auth_path": "", "auth_method": ""}
    )
    assert sm.auth is None


def test_match_path_variations():
    tg = TrafficGenerator(minimal_config(), minimal_sitemap(), Metrics())

    assert tg.match_path("/users/123", "/users/@id") is True
    assert tg.match_path("/users/123/profile", "/users/@id/profile") is True
    assert tg.match_path("/users/123/profile", "/users/@id") is False
    assert tg.match_path("/posts/1", "/users/@id") is False
