"""One optional model request; no automatic retry or UI ownership."""
import asyncio
from collections.abc import Callable
from typing import Protocol

from .diagnostic_events import record_error


class FinishCallback(Protocol):
    """Owns its transaction and verifies identity, token and request ownership.

    Calls must be idempotent: a successful save which then raises can be followed
    by an error call for the same token. That call must preserve the saved result.
    None means the request is absent/inapplicable; it is not proof of a save.
    """
    def __call__(self, identity: str, token: str, response: dict | None = None,
                 *, error: str | None = None) -> dict | None: ...


async def finish_request(operation: str, generate: Callable, client, job: dict,
                         token: str, finish: FinishCallback) -> dict | None:
    """Send once. None can also mean error settlement itself failed; see diagnostics.

    Cancellation is recorded, then re-raised. Callers own UI cleanup and budgets.
    """
    phase = "request"
    try:
        response = await asyncio.to_thread(generate, client, job["request"])
        phase = "save"
        return finish(job["id"], token, response)
    except asyncio.CancelledError as exc:
        record_error(operation, "cancel", exc)
        try:
            finish(job["id"], token, error="request_cancelled")
        except Exception as failure:
            record_error(operation, "record_failure", failure)
        raise
    except Exception as exc:
        record_error(operation, phase, exc)
        try:
            return finish(job["id"], token, error="response_save_failed" if phase == "save" else "request_failed")
        except Exception as failure:
            record_error(operation, "record_failure", failure)
            return None
