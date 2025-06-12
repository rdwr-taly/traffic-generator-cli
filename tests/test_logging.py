import logging
from traffic_generator import (
    ContainerConfig,
    SiteMap,
    PathDefinition,
    TrafficGenerator,
    Metrics,
    console_handler,
    logger,
)


def minimal_config(**overrides):
    base = {
        "Traffic Generator URL": "http://example.com",
        "XFF Header Name": "X-Forwarded-For",
        "Rate Limit": 1,
        "Simulated Users": 1,
        "Minimum Session Length": 1,
        "Maximum Session Length": 1,
        "Debug": True,
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


def test_debug_logging_enabled(caplog):
    TrafficGenerator(minimal_config(), minimal_sitemap(), Metrics())
    assert console_handler.level == logging.DEBUG
    with caplog.at_level(logging.DEBUG, logger="Traffic Generator"):
        logger.debug("debug active")
    assert any(
        rec.levelno == logging.DEBUG and rec.message == "debug active"
        for rec in caplog.records
    )
