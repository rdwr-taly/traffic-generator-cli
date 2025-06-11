import asyncio
from traffic_generator import Metrics


def test_metrics_window_behavior():
    metrics = Metrics()

    async def run_test():
        await metrics.increment()
        await metrics.increment()
        await metrics.increment()
        rps_initial = await metrics.get_rps()
        await asyncio.sleep(1.1)
        rps_after = await metrics.get_rps()
        return rps_initial, rps_after

    rps_initial, rps_after = asyncio.run(run_test())
    assert rps_initial == 3
    assert rps_after == 0
