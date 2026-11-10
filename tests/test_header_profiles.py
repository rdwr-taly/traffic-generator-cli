from traffic_generator import (
    ContainerConfig,
    Metrics,
    PathDefinition,
    SiteMap,
    TrafficGenerator,
)


def _generator():
    cfg = ContainerConfig(
        **{
            "Traffic Generator URL": "http://example.com",
            "XFF Header Name": "X-Forwarded-For",
            "Rate Limit": 1,
            "Simulated Users": 1,
            "Minimum Session Length": 1,
            "Maximum Session Length": 1,
        }
    )
    sm = SiteMap(
        has_auth=False,
        paths=[PathDefinition(method="GET", paths=["/"], traffic_type="web")],
    )
    return TrafficGenerator(cfg, sm, Metrics())


def test_web_profiles_never_send_x_requested_with():
    # A browser page load never carries X-Requested-With; only script-issued
    # (AJAX) requests do. Apps branch on it -- Hackazon's /faq treats any
    # request with it as an AJAX form post and answers 400 "Invalid token!".
    # Every simulated user draws one web profile for its whole life, so a
    # single AJAX-shaped profile turns all of that user's page views into
    # fake form posts.
    offenders = [
        profile
        for profile in _generator().headers_web_options
        if any(name.lower() == "x-requested-with" for name in profile)
    ]
    assert offenders == []
