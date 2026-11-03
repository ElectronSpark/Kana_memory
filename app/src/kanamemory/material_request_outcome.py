"""Material request recovery without taking over budget or transaction ownership."""
from .diagnostic_events import record_error


def record_failure(workflow, run, *, code, delivery_unknown, operation):
    """Attempt token-checked failure settlement once; never resend a request."""
    try:
        workflow().fail(run["id"], run["token"], code=code, delivery_unknown=delivery_unknown)
        return True
    except Exception as exc:
        record_error(operation, "record_failure", exc)
        return False


def refresh_failure(refresh, identity, notify, *, operation):
    """A second presentation failure must not undo a completed transaction."""
    try:
        refresh(identity)
        return True
    except Exception as exc:
        record_error(operation, "refresh", exc)
        notify("页面未能刷新；请重新打开材料任务，已有结果仍保留。")
        return False
