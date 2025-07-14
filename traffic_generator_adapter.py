"""
Traffic Generator Adapter for Container Control Core v2.0.
"""

from __future__ import annotations
import asyncio
import threading
import resource
from typing import Any, Dict, Optional, List

from app_adapter import ApplicationAdapter
from traffic_generator import StartRequest, TrafficGenerator, Metrics, logger


class TrafficGeneratorAdapter(ApplicationAdapter):
    """
    Adapter that integrates the Traffic Generator with Container Control Core v2.0.
    Uses the existing traffic_generator.py implementation with minimal changes.
    Includes all capabilities from the original container_control.py.
    """
    
    # Memory limits (matching original container_control.py)
    MEMORY_SOFT_LIMIT = 4096  # 4GB
    MEMORY_HARD_LIMIT = 4608  # 4.5GB
    
    def __init__(self, static_cfg: Dict[str, Any] | None = None) -> None:
        super().__init__(static_cfg)
        self.traffic_generator: Optional[TrafficGenerator] = None
        self.metrics: Optional[Metrics] = None
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.background_thread: Optional[threading.Thread] = None
        self._loop_running = False
        
        # Set memory limits during initialization (like original)
        self._set_memory_limits()

    def start(self, start_payload: Dict[str, Any], *, ensure_user) -> Any:
        """
        Start the traffic generator with the provided payload.
        Returns the background thread handle for the core to track.
        """
        logger.info("Starting traffic generator...")
        
        # Stop any existing instance first (matching original behavior)
        if self._loop_running:
            logger.info("Stopping existing traffic generator before starting new one...")
            self.stop()
        
        # Validate and parse the payload
        try:
            # Transform payload to match StartRequest structure if needed
            processed_payload = self._process_payload(start_payload)
            start_request = StartRequest(**processed_payload)
        except Exception as e:
            logger.error(f"Failed to parse start payload: {e}")
            raise

        # Initialize metrics
        self.metrics = Metrics()

        # Create traffic generator instance
        self.traffic_generator = TrafficGenerator(
            config=start_request.config,
            site_map=start_request.sitemap,
            metrics=self.metrics
        )

        # Start the traffic generator in a background thread with its own event loop
        self._loop_running = True
        self.background_thread = threading.Thread(
            target=self._run_traffic_loop,
            daemon=True
        )
        self.background_thread.start()

        logger.info("Traffic generator started successfully")
        return self.background_thread

    def stop(self) -> None:
        """Stop the traffic generator gracefully with force stop capability (matching original)."""
        if not self._loop_running:
            logger.warning("Traffic generator not running")
            return

        logger.info("Stopping traffic generator...")
        self._force_stop_traffic_generator(timeout=10)

    def update(self, update_payload: Dict[str, Any]) -> bool:
        """
        Update the traffic generator configuration at runtime.
        Note: This requires stopping and restarting with new config.
        """
        logger.info("Updating traffic generator configuration...")
        
        try:
            # For now, we'll do a restart with new config
            # In the future, this could be made more granular
            self.stop()
            # The ensure_user function isn't available here, so we'll pass a dummy
            self.start(update_payload, ensure_user=lambda x: x)
            return True
        except Exception as e:
            logger.error(f"Failed to update configuration: {e}")
            return False

    def get_metrics(self) -> Dict[str, Any]:
        """Return current traffic generator metrics (enhanced to match original detail level)."""
        if not self.metrics or not self._loop_running:
            return {
                "traffic_generator_status": "stopped",
                "current_rps": 0,
                "running": False,
                "rps": 0.0  # Include for backward compatibility
            }

        try:
            # Get RPS from metrics in a thread-safe way (matching original cross-thread pattern)
            current_rps = 0.0
            if self.event_loop and not self.event_loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self.metrics.get_rps(),
                        self.event_loop
                    )
                    current_rps = float(future.result(timeout=3))  # Match original timeout
                except Exception:
                    current_rps = 0.0

            return {
                "traffic_generator_status": "running",
                "current_rps": current_rps,
                "running": self._loop_running,
                "simulated_users": self.traffic_generator.config.sim_users if self.traffic_generator else 0,
                "rate_limit": self.traffic_generator.config.rate_limit if self.traffic_generator else 0,
                "target_url": self.traffic_generator.config.traffic_target_url if self.traffic_generator else "",
                # Include legacy metric names for compatibility
                "rps": current_rps,
                "metrics": {"rps": current_rps}
            }
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return {
                "traffic_generator_status": "error",
                "current_rps": 0,
                "running": False,
                "error": str(e),
                "rps": 0.0
            }

    def prometheus_metrics(self) -> List[str]:
        """
        Return Prometheus-format metrics (matching original container_control.py format).
        """
        lines = []
        
        # Get current RPS
        current_rps = 0.0
        if self.metrics and self._loop_running and self.event_loop and not self.event_loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.metrics.get_rps(),
                    self.event_loop
                )
                current_rps = float(future.result(timeout=1))
            except Exception:
                current_rps = 0.0

        # Traffic Generator specific metrics (matching original format)
        lines.extend([
            "# HELP traffic_generator_rps Current requests-per-second.",
            "# TYPE traffic_generator_rps gauge",
            f"traffic_generator_rps {current_rps}",
            "# HELP container_rps Current requests-per-second (legacy name).",
            "# TYPE container_rps gauge", 
            f"container_rps {current_rps}",
        ])
        
        # Add traffic generator specific info
        if self.traffic_generator:
            lines.extend([
                "# HELP traffic_generator_simulated_users Number of simulated users.",
                "# TYPE traffic_generator_simulated_users gauge",
                f"traffic_generator_simulated_users {self.traffic_generator.config.sim_users}",
                "# HELP traffic_generator_rate_limit Rate limit setting.",
                "# TYPE traffic_generator_rate_limit gauge",
                f"traffic_generator_rate_limit {self.traffic_generator.config.rate_limit}",
            ])

        return lines

    def _set_memory_limits(self) -> None:
        """Set memory limits for the container process (matching original container_control.py)."""
        MB = 1024 * 1024
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            new_soft = min(self.MEMORY_SOFT_LIMIT * MB, hard)
            resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
            logger.info(f"Memory limits set: Soft={new_soft / MB}MB, Hard={hard / MB}MB")
        except Exception as e:
            logger.error(f"Failed to set memory limits: {e}")

    def _force_stop_traffic_generator(self, timeout: int = 10) -> None:
        """
        Aggressively stop any running traffic generator (matching original force_stop logic).
        """
        if not self._loop_running:
            return

        logger.info("Force stopping traffic generator...")
        self._loop_running = False

        try:
            # Stop the traffic generator if it exists and event loop is running
            if self.event_loop and self.traffic_generator and not self.event_loop.is_closed():
                if self.traffic_generator.running:
                    future = asyncio.run_coroutine_threadsafe(
                        self.traffic_generator.stop_generating(),
                        self.event_loop
                    )
                    try:
                        future.result(timeout=timeout)
                    except Exception as e:
                        logger.error(f"Stop coroutine failed or timed out: {e}")

                # Stop the event loop
                self.event_loop.call_soon_threadsafe(self.event_loop.stop)
        except Exception as e:
            logger.error(f"Error while stopping event loop: {e}")
        finally:
            # Wait for background thread to finish
            if self.background_thread and self.background_thread.is_alive():
                self.background_thread.join(timeout=timeout)
                if self.background_thread.is_alive():
                    logger.warning("Background thread did not terminate cleanly")

            # Clean up (matching original cleanup)
            if self.event_loop and not self.event_loop.is_closed():
                try:
                    self.event_loop.close()
                except Exception as e:
                    logger.error(f"Error closing event loop: {e}")

            self.traffic_generator = None
            self.metrics = None
            self.event_loop = None
            self.background_thread = None
            
            logger.info("Traffic generator force stopped")

    def _run_traffic_loop(self) -> None:
        """
        Run the traffic generator in its own event loop in a background thread.
        Enhanced with better error handling matching the original.
        """
        try:
            # Create new event loop for this thread
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            
            logger.info("Starting traffic generation in background thread...")
            
            # Start the traffic generator
            self.event_loop.run_until_complete(
                self.traffic_generator.start_generating()
            )

            # Keep the loop running until we're told to stop
            # Use a more responsive sleep interval for better shutdown
            while self._loop_running:
                self.event_loop.run_until_complete(asyncio.sleep(0.1))

        except asyncio.CancelledError:
            logger.info("Traffic generation cancelled.")
        except Exception as e:
            logger.error(f"Background traffic generator error: {e}")
        finally:
            logger.info("Background traffic generator thread exiting.")
            # Clean up the event loop (matching original finally block)
            if self.event_loop and not self.event_loop.is_closed():
                try:
                    # Stop any remaining tasks
                    if self.traffic_generator:
                        self.event_loop.run_until_complete(
                            self.traffic_generator.stop_generating()
                        )
                    
                    # Close the loop
                    self.event_loop.close()
                except Exception as e:
                    logger.error(f"Error cleaning up event loop: {e}")

    def _process_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process the incoming payload to ensure it matches StartRequest structure.
        Uses the same logic as the original _ensure_config_sitemap_structure function.
        """
        # Create a copy to avoid modifying the original
        processed = payload.copy()
        
        # Handle sitemap extraction (exactly matching original logic)
        sitemap = processed.pop("sitemap", None)
        
        # If no explicit config, move all non-sitemap keys into config
        config = processed.get("config", {})
        
        # Move all leftover top-level keys into 'config'
        for key in list(processed.keys()):
            if key != "config" and key != "sitemap":
                config[key] = processed.pop(key)
        
        processed["config"] = config
        
        if sitemap is not None:
            # Support newer payload format where sitemap may include metadata under a nested 'sitemap' key
            # (exactly matching original logic)
            if (isinstance(sitemap, dict) and 
                "sitemap" in sitemap and 
                isinstance(sitemap["sitemap"], dict)):
                processed["sitemap"] = sitemap["sitemap"]
            else:
                processed["sitemap"] = sitemap
        
        return processed
