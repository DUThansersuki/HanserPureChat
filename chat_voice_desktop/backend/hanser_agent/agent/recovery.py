from __future__ import annotations

import asyncio
import logging

from ..failures import ServiceFailure


async def recover_post_turn_in_background(agent, *, interval: float = 30.0, batch_size: int = 16) -> None:
    """Attempt each pending job once per app lifetime, in source order.

    Persistent failures remain visible for manual retry or the next startup;
    this worker does not repeatedly run a broken job in a tight loop.
    """
    attempted: set[str] = set()
    while True:
        pending = [row for row in agent.pending_post_turn_failures() if row["id"] not in attempted]
        for row in pending[:batch_size]:
            failure_id = str(row["id"])
            attempted.add(failure_id)
            try:
                await agent.retry_post_turn(failure_id)
            except ServiceFailure:
                logging.getLogger(__name__).warning("post-turn recovery remains pending: %s", failure_id)
        await asyncio.sleep(0 if len(pending) > batch_size else interval)
