"""ShowRunner-managed entry point for Traffic Generator."""

import asyncio
import signal
import logging
import threading

from showrunner_sdk import config, metrics, health
from traffic_generator import StartRequest, TrafficGenerator, Metrics
import sr3_report

logger = logging.getLogger("traffic-generator")
logging.basicConfig(level=logging.INFO)

# -- SDK: App-specific metrics (registration is safe at module level) --
rps_gauge = metrics.gauge("traffic_rps", "Current requests per second")
users_gauge = metrics.gauge("simulated_users", "Number of simulated users")
rate_limit_gauge = metrics.gauge("rate_limit", "Current rate limit")
status_gauge = metrics.gauge("traffic_generator_status", "Generator status (1=running, 0=stopped)")
metrics.set_app_info(name="traffic-generator", version="1.0.0")

generator = None
generator_metrics = None
event_loop = None
run_thread = None
metrics_thread = None
_metrics_running = False

# SR3: last known metrics snapshot + a guard so we write /report/report.json
# exactly once, on the first terminal exit path (signal or main() finally).
_last_snapshot = None
_report_written = False


def write_sr3_report():
    """Write /report/report.json from the latest metrics snapshot.

    Best-effort and idempotent: the first successful write wins so a later
    fallback call (with the metrics already cleared) cannot clobber it with an
    empty report. Never raises.
    """
    global _report_written
    if _report_written:
        return
    snap = None
    try:
        if generator_metrics is not None:
            snap = generator_metrics.snapshot()
        elif _last_snapshot is not None:
            snap = _last_snapshot
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"Failed to read metrics snapshot for SR3 report: {e}")
    if sr3_report.write_report(snap):
        _report_written = True


def _run_metrics_sampler():
    """Periodically sample the generator's Metrics object and push to Prometheus gauges."""
    global _metrics_running
    sample_loop = asyncio.new_event_loop()
    while _metrics_running:
        if generator_metrics and event_loop and not event_loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    generator_metrics.get_rps(), event_loop
                )
                rps = float(future.result(timeout=2))
                rps_gauge.set(rps)
            except Exception:
                rps_gauge.set(0)
        sample_loop.run_until_complete(asyncio.sleep(1))
    sample_loop.close()


def start_generator(cfg_data):
    """Parse config, create TrafficGenerator, run it in a background thread."""
    global generator, generator_metrics, event_loop, run_thread
    global metrics_thread, _metrics_running

    stop_generator()

    try:
        start_request = StartRequest(**cfg_data)
    except Exception as e:
        logger.error(f"Invalid config: {e}")
        health.set_status("error", reason=f"bad config: {e}")
        return

    generator_metrics = Metrics()
    generator = TrafficGenerator(
        config=start_request.config,
        site_map=start_request.sitemap,
        metrics=generator_metrics,
    )

    users_gauge.set(start_request.config.sim_users)
    rate_limit_gauge.set(start_request.config.rate_limit)

    def run():
        global event_loop
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        try:
            event_loop.run_until_complete(generator.start_generating())
            # Keep the loop alive so tasks continue running
            while generator and generator.running:
                event_loop.run_until_complete(asyncio.sleep(0.5))
        except Exception as e:
            logger.error(f"Generator error: {e}")
            health.set_status("error", reason=str(e))
        finally:
            if event_loop and not event_loop.is_closed():
                event_loop.close()

    run_thread = threading.Thread(target=run, name="traffic-generator", daemon=True)
    run_thread.start()

    # Start the metrics sampler thread
    _metrics_running = True
    metrics_thread = threading.Thread(
        target=_run_metrics_sampler, name="metrics-sampler", daemon=True
    )
    metrics_thread.start()

    health.set_status("running")
    status_gauge.set(1)
    logger.info("Traffic generator started")


def stop_generator():
    """Stop the generator and clean up threads."""
    global generator, generator_metrics, event_loop, run_thread
    global metrics_thread, _metrics_running, _last_snapshot

    _metrics_running = False

    # SR3: preserve the final counters before the metrics object is cleared so
    # the report writer still has data even if it runs after this teardown.
    if generator_metrics is not None:
        try:
            _last_snapshot = generator_metrics.snapshot()
        except Exception:
            pass

    if generator and event_loop and not event_loop.is_closed():
        try:
            future = asyncio.run_coroutine_threadsafe(
                generator.stop_generating(), event_loop
            )
            future.result(timeout=10)
        except Exception as e:
            logger.error(f"Error stopping generator: {e}")

    if event_loop and not event_loop.is_closed():
        event_loop.call_soon_threadsafe(event_loop.stop)

    if run_thread:
        run_thread.join(timeout=5)
    if metrics_thread:
        metrics_thread.join(timeout=2)

    generator = None
    generator_metrics = None
    event_loop = None
    run_thread = None
    metrics_thread = None

    rps_gauge.set(0)
    users_gauge.set(0)
    rate_limit_gauge.set(0)
    status_gauge.set(0)
    health.set_status("stopped")


# -- Lifecycle --
shutdown = threading.Event()


def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    stop_generator()
    # SR3: seal the report once traffic has stopped and counters are final.
    write_sr3_report()
    shutdown.set()


def main():
    # Start metrics + health server on :9090
    metrics.start_server()

    # Load initial config
    cfg = config.load()

    # Register config reload handler (called on SIGHUP)
    config.on_reload(lambda new_cfg: start_generator(new_cfg))

    # Set initial health status
    health.set_status("starting")

    # Register shutdown handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Start if config available
    if cfg:
        start_generator(cfg)
    else:
        health.set_status("waiting", reason="no config")
        logger.info("Waiting for config at /config/app.json...")

    # Block until shutdown
    try:
        shutdown.wait()
    finally:
        # SR3: catch-all so the report is written even on an unexpected exit.
        # Idempotent — handle_shutdown() has usually already sealed it.
        write_sr3_report()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
