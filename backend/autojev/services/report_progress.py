"""Ephemeral, scoped export telemetry; never starts browser or model work."""
import asyncio
from threading import Event, Lock
from time import monotonic
from uuid import UUID

from fastapi import HTTPException


class ExportCancelled(Exception):
    pass


class Preparation:
    def __init__(self, scope):
        self.scope = scope
        self.cancelled = Event()
        self.lock = Lock()
        self.updated = monotonic()
        self.state = {"stage": "preparing", "completed": 0, "total": None}

    def check(self):
        if self.cancelled.is_set():
            raise ExportCancelled()

    def update(self, stage, completed=0, total=None):
        self.check()
        with self.lock:
            self.state = {"stage": stage, "completed": completed, "total": total}
            self.updated = monotonic()

    def snapshot(self):
        with self.lock:
            return dict(self.state)


class ReportProgress:
    def __init__(self):
        self.entries = {}

    def begin(self, identity, scope):
        try:
            UUID(identity)
        except ValueError:
            raise HTTPException(422, "Invalid export identifier") from None
        now = monotonic()
        self.entries = {key: value for key, value in self.entries.items()
                        if now - value.updated < 3600}
        if identity in self.entries:
            raise HTTPException(409, "Export identifier already used")
        if len(self.entries) >= 256:
            raise HTTPException(429, "Too many exports")
        progress = Preparation(scope)
        self.entries[identity] = progress
        return progress


async def tracked_export(request, identity, scope, work):
    if identity is None:
        return await work(None)
    progress = request.app.state.test_suite_service.report_progress.begin(identity, scope)

    async def disconnected():
        while True:
            if await request.is_disconnected():
                progress.cancelled.set()
                return
            await asyncio.sleep(0.25)

    monitor = asyncio.create_task(disconnected())
    try:
        response = await work(progress)
        progress.update("ready", 1, 1)
        return response
    except BaseException as error:
        with progress.lock:
            progress.state = {"stage": "cancelled" if progress.cancelled.is_set() else "error", "completed": 0, "total": None}
        if isinstance(error, ExportCancelled):
            raise HTTPException(499, "Export cancelled") from None
        raise
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
