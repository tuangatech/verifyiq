"""SSE streaming for live verification pipeline progress."""

import asyncio
import json
from typing import AsyncGenerator

import structlog

logger = structlog.get_logger()


class SSEStreamer:
    """Streams SSE events for a verification task.

    Responsibilities:
    1. Maintain a asyncio.Queue per task_id for live event delivery
    2. Persist every event to sse_events table (via TaskManager)
    3. Stream events to clients — with catch-up replay for late connectors
    """

    def __init__(self, task_manager):
        self._task_manager = task_manager
        self._queues: dict[str, asyncio.Queue] = {}
        self._done: dict[str, bool] = {}
        self._queues_lock = asyncio.Lock()
        self._seq: dict[str, int] = {}

    async def create_stream(self, task_id: str) -> None:
        """Called at pipeline start; allocates a queue for this task."""
        async with self._queues_lock:
            self._queues[task_id] = asyncio.Queue(maxsize=100)
            self._done[task_id] = False
            self._seq[task_id] = 0

    async def emit(
        self,
        task_id: str,
        correlation_id: str,
        event_type: str,
        payload: dict,
    ) -> None:
        """Write event to DB and push to queue.

        Called at each milestone in run_verification.
        """
        # Include event_type, correlation_id, and sequence number in the event payload
        self._seq[task_id] = self._seq.get(task_id, 0) + 1
        event_payload = payload.copy()
        event_payload["event_type"] = event_type
        event_payload["correlation_id"] = correlation_id
        event_payload["_seq"] = self._seq[task_id]

        # Persist to DB
        self._task_manager.write_sse_event(
            correlation_id=correlation_id,
            event_type=event_type,
            payload=json.dumps(event_payload),
        )

        # Push to queue (non-blocking if full — pipeline continues)
        try:
            await asyncio.wait_for(
                self._queues[task_id].put(event_payload),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            logger.warning("sse_queue_full", task_id=task_id)

    async def complete(self, task_id: str) -> None:
        """Push sentinel None to queue and set done flag."""
        async with self._queues_lock:
            if task_id in self._queues:
                try:
                    await asyncio.wait_for(
                        self._queues[task_id].put(None),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    pass
            self._done[task_id] = True

    async def stream(
        self,
        task_id: str,
        correlation_id: str,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE-formatted strings.

        Replays from DB first (for late connectors), then yields live from queue.
        Stops on sentinel None or when done flag is set.
        """
        replayed = False

        async with self._queues_lock:
            queue = self._queues.get(task_id)
            done = self._done.get(task_id, True)

        if done:
            # Pipeline already finished — pure replay from DB
            events = self._task_manager.get_sse_events(correlation_id)
            for event in events:
                payload = json.loads(event["payload"])
                event_str = self._format_event(event["event_type"], payload)
                yield event_str
            return

        if queue and not done:
            # Replay already-emitted events from DB (for late connectors mid-pipeline)
            events = self._task_manager.get_sse_events(correlation_id)
            for event in events:
                payload = json.loads(event["payload"])
                event_str = self._format_event(event["event_type"], payload)
                yield event_str

            replayed = True

        # Stream live from queue
        if queue and not done:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=5.0,  # 5s timeout to check for completion
                    )

                    # Sentinel — no more events
                    if event is None:
                        break

                    # Event already contains correlation_id from emit()
                    event_str = self._format_event(event["event_type"], event)
                    yield event_str

                except asyncio.TimeoutError:
                    # Timeout waiting — check if done
                    if self._done.get(task_id):
                        # Done flag set — no more events coming
                        break
                    # Still running — keep waiting

    def _format_event(self, event_type: str, payload: dict) -> str:
        """Format an event as SSE string.

        Wire format:
        event: <type>
        data: <JSON>
        id: <sequence>

        """
        seq = payload.get("_seq", 0)
        line1 = f"event: {event_type}"
        line2 = f"data: {json.dumps(payload)}"
        line3 = f"id: {seq}"
        return f"{line1}\n{line2}\n{line3}\n\n"
