"""Serialize pipeline changes in the single application process (run.py)."""

from functools import wraps
from threading import RLock

pipeline_lock = RLock()


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with pipeline_lock:
            return function(*args, **kwargs)

    return wrapped
