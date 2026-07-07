"""SR3 report writer — emit ``/report/report.json`` for ShowRunner to pull.

ShowRunner v3.0 pulls this file out of the container at window close (via the
Docker API) and projects its ``measures`` into the demo report + runbook. The
app declares this contract in its ``.showrunner/appspec.json`` ``sdk`` block, so
ShowRunner knows the path and what measures to expect.

Fully optional and non-fatal: if the path is not writable the run is unaffected
(ShowRunner simply degrades to Tier-0, i.e. Prometheus metrics + logs). The file
is written atomically (tmp + rename) with ``status: "final"`` so ShowRunner never
observes a half-written report.

Traffic Generator emits *legit-shaped* web/API traffic (not attacks), so the
report frames outcomes around target health under load: how many responses came
back, how many were errors (HTTP >= 400), and the resulting error ratio. The
numbers come straight from the app's own ``Metrics`` object, which counts every
HTTP response by status code in the request path — nothing is invented here.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = "/report/report.json"


def _coerce_snapshot(snapshot: Optional[dict]) -> dict[str, Any]:
    """Normalize a Metrics snapshot into by_code / total / errors.

    Accepts the dict returned by ``traffic_generator.Metrics.snapshot()``:
    ``{"status_counts": {"200": n, ...}, "total": int, "errors": int}``.
    Falls back to deriving totals from ``status_counts`` when the roll-ups are
    missing, and to an empty report when no snapshot is available.
    """
    if not snapshot:
        return {"by_code": {}, "total": 0, "errors": 0}

    by_code_raw = snapshot.get("status_counts") or {}
    by_code: dict[str, int] = {}
    for code, count in by_code_raw.items():
        try:
            by_code[str(code)] = int(count)
        except (TypeError, ValueError):
            continue

    total = snapshot.get("total")
    if total is None:
        total = sum(by_code.values())
    errors = snapshot.get("errors")
    if errors is None:
        errors = sum(
            v for code, v in by_code.items() if code.isdigit() and int(code) >= 400
        )

    return {"by_code": by_code, "total": int(total), "errors": int(errors)}


def build_report(snapshot: Optional[dict]) -> dict[str, Any]:
    """Build the SR3 report document from a Metrics snapshot."""
    data = _coerce_snapshot(snapshot)
    by_code = data["by_code"]
    total = data["total"]
    errors = data["errors"]
    error_ratio = round(errors / total, 4) if total else 0.0

    if total:
        summary = (
            f"Generated {total} response(s) under legitimate load; "
            f"{errors} error response(s) ({error_ratio:.0%} error ratio). "
            + (
                "Target stayed healthy under traffic."
                if error_ratio < 0.5
                else "Target degraded under traffic (elevated error ratio)."
            )
        )
    else:
        summary = "No responses were observed."

    findings: list[dict[str, str]] = []
    if total and error_ratio >= 0.5:
        findings.append(
            {
                "severity": "warning",
                "title": "Target degraded under legitimate load",
                "category": "availability",
                "detail": (
                    f"{errors} of {total} responses were errors (HTTP >= 400), "
                    f"an error ratio of {error_ratio:.0%}. A healthy target should "
                    f"absorb legit-shaped traffic with a low error ratio."
                ),
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measures": {
            "responses.by_code": by_code,
            "responses.total": total,
            "responses.errors": errors,
            "responses.error_ratio": error_ratio,
        },
        "summary": summary,
    }
    if findings:
        report["findings"] = findings
    return report


def write_report(snapshot: Optional[dict], path: Optional[str] = None) -> bool:
    """Atomically write the SR3 report. Returns True on success, never raises."""
    target = Path(path or os.getenv("SR_REPORT_PATH", DEFAULT_REPORT_PATH))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(build_report(snapshot), indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic rename on the same filesystem
        LOGGER.info("SR3 report written to %s", target)
        return True
    except Exception:  # pragma: no cover - degrade to Tier-0, never affect the run
        LOGGER.debug(
            "SR3 report write failed; ShowRunner will degrade to Tier-0", exc_info=True
        )
        return False


__all__ = ["build_report", "write_report", "DEFAULT_REPORT_PATH"]
