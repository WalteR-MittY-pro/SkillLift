"""Machine-local model slots, shared across experiment commands and processes."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import time

MODEL_LIMITS = {"glm": 4, "deepseek": 8}


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
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = handle
                    break
                except BlockingIOError:
                    continue
            if acquired is None:
                time.sleep(0.2)
        yield
    finally:
        if acquired is not None:
            fcntl.flock(acquired, fcntl.LOCK_UN)
        for handle in handles:
            handle.close()
