"""Machine-local model slots, shared across experiment commands and processes."""
from contextlib import contextmanager
from pathlib import Path
import time

from .common import file_lock, try_lock_handle, unlock_handle

MODEL_LIMITS = {"glm": 2, "deepseek": 2}


def pool_name(model):
    if model.startswith("glm"):
        return "glm"
    if model.startswith("deepseek"):
        return "deepseek"
    return "other"


@contextmanager
def model_slot(model, root=None):
    pool = pool_name(model)
    folder = (Path(root) if root else Path.home() / ".cache/skilllift/model-slots") / pool
    folder.mkdir(parents=True, exist_ok=True)
    handles = [(folder / f"slot-{i}.lock").open("a+") for i in range(MODEL_LIMITS.get(pool, 4))]
    acquired = None
    try:
        while acquired is None:
            for handle in handles:
                if try_lock_handle(handle):
                    acquired = handle
                    break
            if acquired is None:
                time.sleep(0.2)
        yield
    finally:
        if acquired is not None:
            unlock_handle(acquired)
        for handle in handles:
            handle.close()


API_REQUEST_INTERVAL_SECONDS = 0.5


def wait_for_api_request(model, root=None, interval=API_REQUEST_INTERVAL_SECONDS):
    """Space request starts across processes, without locking during HTTP I/O.

    Each provider pool has its own clock. An idle pool incurs no extra delay.
    The separate lock file remains stable while the timestamp is overwritten.
    """
    folder = (Path(root) if root else Path.home() / ".cache/skilllift/api-pacing") / pool_name(model)
    with file_lock(folder / "request.lock"):
        stamp = folder / "last-start"
        now = time.monotonic()
        try:
            previous = float(stamp.read_text())
        except (FileNotFoundError, ValueError):
            previous = None
        # A future monotonic timestamp can survive a machine reboot.
        if previous is not None and 0 <= previous <= now:
            remaining = interval - (now - previous)
            if remaining > 0:
                time.sleep(remaining)
        started = time.monotonic()
        stamp.write_text(str(started))
    return started
