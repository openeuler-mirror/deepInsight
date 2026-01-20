"""Useful class and function to implement singleton."""
import threading
from typing import TypeVar, Type

_instances = {}
_init_locks: dict[type, threading.Lock] = {}
_add_class_lock = threading.Lock()
_T = TypeVar("_T")


class SingletonMeta(type):
    def __call__(cls, *args, **kwargs):
        with _add_class_lock:
            if cls not in _init_locks:
                _init_locks[cls] = threading.Lock()

        with _init_locks[cls]:
            if cls not in _instances:
                _instances[cls] = super().__call__(*args, **kwargs)
            return _instances[cls]


def make_singleton(cls: Type[_T], *args, **kwargs) -> _T:
    with _add_class_lock:
        if cls not in _init_locks:
            _init_locks[cls] = threading.Lock()

    with _init_locks[cls]:
        if cls not in _instances:
            _instances[cls] = cls(*args, **kwargs)
        return _instances[cls]
