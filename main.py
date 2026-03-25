"""ShowRunner-managed entry point for Traffic Generator."""

import asyncio
import signal
import logging
import threading

from showrunner_sdk import config, metrics, health
from traffic_generator import StartRequest, TrafficGenerator, Metrics

logger = logging.getLogger("traffic-generator")
logging.basicConfig(level=logging.INFO)

# -- SDK: App-specific metrics --
rps_gauge = metrics.gauge("traffic_rps", "Current requests per second")
users_gauge = metrics.gauge("simulated_users", "Number of simulated users")
rate_limit_gauge = metrics.gauge("rate_limit", "Current rate limit")
status_gauge = metrics.gauge("traffic_generator_status", "Generator status (1=running, 0=stopped)")
metrics.set_app_info(name="traffic-generator", version="1.0.0")

# -- SDK: Start metrics server (port 9090) --
metrics.start_server()

generator = None
generator_metrics = None
event_loop = None
run_thread = None
metrics_thread = None
_metrics_running = False


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
    global metrics_thread, _metrics_running

    _metrics_running = False

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


# -- SDK: Config --
cfg = config.load()

# Register config reload handler (called on SIGHUP)
config.on_reload(lambda new_cfg: start_generator(new_cfg))

# Start if config available
if cfg:
    start_generator(cfg)
else:
    health.set_status("waiting", reason="no config")
    logger.info("Waiting for config at /config/app.json...")

# -- Keep alive: wait for SIGTERM/SIGINT --
shutdown = threading.Event()


def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    stop_generator()
    shutdown.set()


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)
shutdown.wait()
