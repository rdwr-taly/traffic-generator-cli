"""Tests for the SR3 /report/report.json writer."""

import asyncio
import json

import sr3_report
from traffic_generator import Metrics


def _populated_metrics():
    """Build a Metrics object with a realistic per-status-code distribution."""
    metrics = Metrics()

    async def run():
        for _ in range(90):
            await metrics.record_status(200)
        for _ in range(5):
            await metrics.record_status(301)
        for _ in range(3):
            await metrics.record_status(404)
        for _ in range(2):
            await metrics.record_status(500)

    asyncio.run(run())
    return metrics


def test_metrics_snapshot_counts_by_code():
    metrics = _populated_metrics()
    snap = metrics.snapshot()
    assert snap["status_counts"] == {"200": 90, "301": 5, "404": 3, "500": 2}
    assert snap["total"] == 100
    # errors = HTTP >= 400 -> 404 (3) + 500 (2)
    assert snap["errors"] == 5


def test_build_report_measures_and_shape():
    snap = _populated_metrics().snapshot()
    report = sr3_report.build_report(snap)

    assert report["schema_version"] == 1
    assert report["status"] == "final"
    assert "generated_at" in report

    m = report["measures"]
    assert m["responses.by_code"] == {"200": 90, "301": 5, "404": 3, "500": 2}
    assert m["responses.total"] == 100
    assert m["responses.errors"] == 5
    assert m["responses.error_ratio"] == 0.05  # 5 / 100
    assert "healthy" in report["summary"].lower()
    # Low error ratio -> no availability finding.
    assert "findings" not in report


def test_build_report_flags_degraded_target():
    metrics = Metrics()

    async def run():
        for _ in range(4):
            await metrics.record_status(200)
        for _ in range(6):
            await metrics.record_status(503)

    asyncio.run(run())
    report = sr3_report.build_report(metrics.snapshot())

    assert report["measures"]["responses.error_ratio"] == 0.6
    assert report["findings"], "expected an availability finding at high error ratio"
    assert report["findings"][0]["severity"] == "warning"
    assert report["findings"][0]["category"] == "availability"


def test_build_report_empty_run():
    report = sr3_report.build_report(None)
    assert report["status"] == "final"
    assert report["measures"]["responses.total"] == 0
    assert report["measures"]["responses.error_ratio"] == 0.0
    assert report["measures"]["responses.by_code"] == {}


def test_write_report_atomic_and_sealed(tmp_path):
    target = tmp_path / "sub" / "report.json"  # parent does not exist yet
    snap = _populated_metrics().snapshot()

    ok = sr3_report.write_report(snap, path=str(target))
    assert ok is True
    assert target.exists()
    assert not target.with_name(target.name + ".tmp").exists()  # tmp cleaned up

    doc = json.loads(target.read_text())
    assert doc["status"] == "final"  # sealed
    assert doc["measures"]["responses.total"] == 100


def test_write_report_never_raises_on_bad_path():
    # A path whose parent cannot be created (a file stands where a dir is needed)
    # must degrade to False, never raise.
    assert sr3_report.write_report({}, path="/dev/null/nope/report.json") is False
