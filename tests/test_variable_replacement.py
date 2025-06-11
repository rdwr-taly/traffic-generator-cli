from traffic_generator import (
    TrafficGenerator,
    Metrics,
    ContainerConfig,
    SiteMap,
    PathDefinition,
    VariableDefinition,
)


def test_replace_variables(monkeypatch):
    config = ContainerConfig(
        **{
            "Traffic Generator URL": "http://example.com",
            "XFF Header Name": "X-Forwarded-For",
            "Rate Limit": 1,
            "Simulated Users": 1,
            "Minimum Session Length": 1,
            "Maximum Session Length": 1,
        }
    )
    site_map = SiteMap(
        has_auth=False,
        paths=[PathDefinition(method="GET", paths=["/"], traffic_type="web")],
        variables={
            "id": VariableDefinition(type="list", value=["123"]),
            "age": VariableDefinition(type="range", value=[10, 20]),
        },
    )
    tg = TrafficGenerator(config, site_map, Metrics())

    monkeypatch.setattr("random.choice", lambda x: x[0])
    monkeypatch.setattr("random.randint", lambda a, b: 15)

    assert tg.replace_variables("/user/@id") == "/user/123"
    assert tg.replace_variables("age=@age") == "age=15"

    data = {
        "url": "/user/@id",
        "info": {"age": "@age"},
        "list": ["@id", {"a": "@age"}],
    }
    replaced = tg._replace_variables_in_dict(data)
    assert replaced["url"] == "/user/123"
    assert replaced["info"]["age"] == "15"
    assert replaced["list"] == ["123", {"a": "15"}]
