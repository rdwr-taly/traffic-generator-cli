# container_control.py

import threading
import time
import logging
import signal
import os
import psutil
import uvicorn
from datetime import datetime
import resource
from typing import Any

# Import all traffic generator functionality
from traffic_generator import (
    StartRequest,
    TrafficGenerator,
    Metrics,
    asyncio,
    logger as tg_logger,
)

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Dict, Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

# ------------------------------------------------------
# Global Runtime State
# ------------------------------------------------------
current_settings = {
    "app_status": "initializing",  # Initialize to 'initializing'
    "container_status": "running",
}

traffic_generator_instance = None
event_loop = None
background_thread = None

# Memory limits (if you want them)
MEMORY_SOFT_LIMIT = 4096  # 4GB
MEMORY_HARD_LIMIT = 4608  # 4.5GB


# ---------------------------------------------------------------------
# HELPER: ensures the posted data has the shape your StartRequest expects
# ---------------------------------------------------------------------
def _ensure_config_sitemap_structure(data: dict) -> dict:
    """
    If the incoming data does NOT already have a "config" key,
    then create one from all non-'sitemap' keys.
    If "sitemap" exists, keep it separate.
    Result => { "config": {...}, "sitemap": {...} }
    """
    sitemap = data.pop("sitemap", None)

    config = data.get("config", {})

    # Move all leftover top-level keys into 'config'
    for key in list(data.keys()):
        if key != "config" and key != "sitemap":
            config[key] = data.pop(key)

    data["config"] = config

    if sitemap is not None:
        # Support newer payload format where sitemap may include metadata under a nested 'sitemap' key
        if (
            isinstance(sitemap, dict)
            and "sitemap" in sitemap
            and isinstance(sitemap["sitemap"], dict)
        ):
            data["sitemap"] = sitemap["sitemap"]
        else:
            data["sitemap"] = sitemap

    return data


def set_memory_limits():
    """Set memory limits for the container process (if supported by the system)."""
    MB = 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        new_soft = min(MEMORY_SOFT_LIMIT * MB, hard)
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
        logger.info(f"Memory limits set: Soft={new_soft / MB}MB, Hard={hard / MB}MB")
    except Exception as e:
        logger.error(f"Failed to set memory limits: {e}")


def run_traffic_generator_in_loop(config, sitemap):
    """
    Runs in a dedicated background thread.
    Creates an asyncio loop, instantiates the traffic generator,
    and starts traffic until /api/stop is called.
    """
    global event_loop, traffic_generator_instance

    try:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        traffic_generator_instance = TrafficGenerator(config, sitemap, Metrics())
        logger.info("Starting traffic generation...")
        event_loop.run_until_complete(traffic_generator_instance.start_generating())
        # Keep the loop running until explicitly stopped
        event_loop.run_forever()
    except asyncio.CancelledError:
        logger.info("Traffic generation cancelled.")
    except Exception as e:
        logger.error(f"Background traffic generator error: {e}")
    finally:
        logger.info("Background traffic generator thread exiting.")
        if event_loop and not event_loop.is_closed():
            event_loop.close()


def force_stop_traffic_generator(timeout: int = 5):
    """Aggressively stop any running traffic generator."""
    global event_loop, traffic_generator_instance, background_thread

    if not traffic_generator_instance or not event_loop or not background_thread:
        return

    current_settings["app_status"] = "stopping"
    logger.info("Force stopping running traffic generator...")

    try:
        if event_loop.is_running() and traffic_generator_instance.running:
            future = asyncio.run_coroutine_threadsafe(
                traffic_generator_instance.stop_generating(), event_loop
            )
            try:
                future.result(timeout=timeout)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Stop coroutine failed or timed out: {e}")

        event_loop.call_soon_threadsafe(event_loop.stop)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error while stopping event loop: {e}")
    finally:
        if background_thread.is_alive():
            background_thread.join(timeout=timeout)

        if not event_loop.is_closed():
            event_loop.close()

        traffic_generator_instance = None
        event_loop = None
        background_thread = None
        current_settings["app_status"] = "stopped"


@app.get("/api/health")
async def health_check():
    """Basic health check endpoint."""
    return JSONResponse({"status": "healthy"})


class StartRequestWrapper(BaseModel):
    config: Dict[str, Any]
    sitemap: Dict[str, Any]


@app.post("/api/start")
async def start_traffic_generator(data: dict):
    """
    Start the traffic generation in a background thread.
    We expect the final data shape to have "config" and "sitemap".
    But if data doesn't come that way, we fix it with _ensure_config_sitemap_structure.
    """
    global background_thread

    if current_settings["app_status"] == "running":
        force_stop_traffic_generator()
    # Read incoming JSON and correct if needed
    try:

        data = _ensure_config_sitemap_structure(data)
        # Now parse the validated data
        req_obj = StartRequest(**data)
    except Exception as e:
        logger.error(f"Invalid request body: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")

    current_settings["app_status"] = "running"

    background_thread = threading.Thread(
        target=run_traffic_generator_in_loop,
        args=(req_obj.config, req_obj.sitemap),
        daemon=True,
    )
    background_thread.start()

    return JSONResponse({"message": "Traffic generator started"})


@app.post("/api/stop")
async def stop_traffic_generator():
    global event_loop, traffic_generator_instance, background_thread

    if (
        current_settings["app_status"] != "running"
        or traffic_generator_instance is None
    ):
        raise HTTPException(status_code=400, detail="No traffic generator is running")

    force_stop_traffic_generator()

    return JSONResponse({"message": "Traffic generator stopped"})


@app.get("/api/metrics")
async def api_metrics():
    """
    Return combined container + traffic generator stats.
    """
    container_cpu_percent = psutil.cpu_percent(interval=0.2)
    container_mem = psutil.virtual_memory()
    net_io = psutil.net_io_counters()
    bytes_sent = net_io.bytes_sent
    bytes_recv = net_io.bytes_recv
    packets_sent = net_io.packets_sent
    packets_recv = net_io.packets_recv

    rps_val = 0.0
    if traffic_generator_instance and traffic_generator_instance.running:

        async def get_rps():
            return await traffic_generator_instance.metrics.get_rps()

        if event_loop and not event_loop.is_closed():
            try:
                rps_val = asyncio.run_coroutine_threadsafe(
                    get_rps(), event_loop
                ).result(timeout=3)
            except:
                rps_val = 0.0

    resp_body = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "app_status": current_settings["app_status"],
        "container_status": current_settings["container_status"],
        "network": {
            "bytes_sent": bytes_sent,
            "bytes_recv": bytes_recv,
            "packets_sent": packets_sent,
            "packets_recv": packets_recv,
        },
        "system": {
            "cpu_percent": round(container_cpu_percent, 1),
            "memory_percent": round(container_mem.percent, 1),
            "memory_available_mb": round(container_mem.available / (1024 * 1024), 2),
            "memory_used_mb": round(container_mem.used / (1024 * 1024), 2),
        },
        "metrics": {"rps": float(rps_val)},
    }
    return JSONResponse(resp_body)


@app.get("/metrics")
async def metrics_prometheus():
    """
    Prometheus /metrics with combined container + traffic generator stats.
    """
    container_cpu_percent = psutil.cpu_percent(interval=0.2)
    container_mem = psutil.virtual_memory()
    net_io = psutil.net_io_counters()
    bytes_sent = net_io.bytes_sent
    bytes_recv = net_io.bytes_recv
    packets_sent = net_io.packets_sent
    packets_recv = net_io.packets_recv

    rps_val = 0.0
    if traffic_generator_instance and traffic_generator_instance.running:

        async def get_rps():
            return await traffic_generator_instance.metrics.get_rps()

        if event_loop and not event_loop.is_closed():
            try:
                rps_val = asyncio.run_coroutine_threadsafe(
                    get_rps(), event_loop
                ).result(timeout=3)
            except:
                rps_val = 0.0
    lines = [
        "# HELP container_cpu_percent CPU usage percent.",
        "# TYPE container_cpu_percent gauge",
        f"container_cpu_percent {round(container_cpu_percent, 1)}",
        "# HELP container_memory_percent Memory usage percent.",
        "# TYPE container_memory_percent gauge",
        f"container_memory_percent {round(container_mem.percent, 1)}",
        "# HELP container_memory_available_mb Memory available in MB.",
        "# TYPE container_memory_available_mb gauge",
        f"container_memory_available_mb {round(container_mem.available / (1024 * 1024), 2)}",
        "# HELP container_memory_used_mb Memory used in MB.",
        "# TYPE container_memory_used_mb gauge",
        f"container_memory_used_mb {round(container_mem.used / (1024 * 1024), 2)}",
        "# HELP container_network_bytes_sent Bytes sent.",
        "# TYPE container_network_bytes_sent counter",
        f"container_network_bytes_sent {bytes_sent}",
        "# HELP container_network_bytes_recv Bytes received.",
        "# TYPE container_network_bytes_recv counter",
        f"container_network_bytes_recv {bytes_recv}",
        "# HELP container_network_packets_sent Packets sent.",
        "# TYPE container_network_packets_sent counter",
        f"container_network_packets_sent {packets_sent}",
        "# HELP container_network_packets_recv Packets received.",
        "# TYPE container_network_packets_recv counter",
        f"container_network_packets_recv {packets_recv}",
        "# HELP traffic_generator_rps Current requests-per-second.",
        "# TYPE traffic_generator_rps gauge",
        f"container_rps {rps_val}",
        "# HELP app_status Application status.",  # Add app_status metric
        "# TYPE app_status gauge",  #
        f"app_status{{status=\"initializing\"}} {1 if current_settings['app_status'] == 'initializing' else 0}",  # Correct status handling
        f"app_status{{status=\"stopped\"}} {1 if current_settings['app_status'] == 'stopped' else 0}",  #
        f"app_status{{status=\"running\"}} {1 if current_settings['app_status'] == 'running' else 0}",  #
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain")


def handle_signal(signum, frame):
    """
    Handle SIGTERM/SIGINT to gracefully stop the traffic generator.
    """
    logger.info(f"Received signal {signum}; shutting down container_control.")
    force_stop_traffic_generator()
    os._exit(0)


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

if __name__ == "__main__":
    set_memory_limits()
    import uvicorn

    uvicorn.run("container_control:app", host="0.0.0.0", port=8080, reload=True)
