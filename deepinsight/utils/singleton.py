"""Useful class and function to implement singleton."""
import threading

_instances = {}
_init_locks: dict[type, threading.Lock] = {}
_add_class_lock = threading.Lock()


class SingletonMeta(type):
    def __call__(cls, *args, **kwargs):
        with _add_class_lock:
            if cls not in _init_locks:
                _init_locks[cls] = threading.Lock()

        with _init_locks[cls]:
            if cls not in _instances:
                _instances[cls] = super().__call__(*args, **kwargs)
            return _instances[cls]
