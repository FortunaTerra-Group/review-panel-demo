import time


def now() -> float:
    """Single seam for wall-clock reads so tests can drive window boundaries deterministically."""
    return time.time()
