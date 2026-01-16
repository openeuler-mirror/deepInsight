__all__ = ["tracepoint"]

import asyncio
import functools
import inspect
import logging
import os
import threading
from typing import Any, Callable, Generic, Iterable, Literal, Mapping, ParamSpec, Protocol, TypeVar, no_type_check, overload

from langchain_core.runnables import RunnableLambda
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.runnables.config import RunnableConfig, set_config_context
from langchain_core.runnables.utils import coro_with_context

_P = ParamSpec("_P")
_T = TypeVar("_T")
_Func = TypeVar("_Func", bound=Callable)
logger = logging.getLogger(__name__)


class _TracedFunc(Protocol[_Func]):
    with_trace: _Func


class _Tracepoint(Protocol[_P, _T]):
    def with_trace(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        ...

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        ...


class _TracepointBase(Generic[_P, _T]):
    __call__: Callable
    _Method: "_TracepointAsync._Method | _TracepointSync._Method"  # noqa: for type hint

    def __init__(self, f: Callable[_P, _T], name: str,
                 invisible_args: frozenset[str], arg_replacement: Mapping[str, Callable[[Any], Any]]):
        functools.update_wrapper(self, f)
        self.__signature__ = inspect.signature(f)
        self._invisible_args = invisible_args
        self._arg_replacement = arg_replacement
        self._f = f
        self._name = name

    def __get__(self, owner_obj, owner_cls):
        if owner_obj is None:
            return self
        return self._Method(owner_obj, self)


class _TracepointSync(_TracepointBase):
    class _Method:
        def __init__(self, method_owner, tracepoint_: "_TracepointSync"):
            self._owner = method_owner
            self._tp = tracepoint_

        def __call__(self, *args, **kwargs) -> _T:
            return self._tp(self._owner, *args, **kwargs)

        def with_trace(self, *args, **kwargs) -> _T:
            return self._tp.with_trace(self._owner, *args, **kwargs)

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        input_dict = _make_traced_input(self.__signature__, args, kwargs, self._invisible_args, self._arg_replacement)

        def runner(inputs: dict):
            _ = inputs
            return self._f(*args, **kwargs)

        return RunnableLambda(runner, name=self._name).invoke(input_dict)

    def with_trace(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        with set_config_context(RunnableConfig(callbacks=_get_callbacks())) as ctx:
            return ctx.run(self, *args, **kwargs)


class _TracepointAsync(_TracepointBase):
    class _Method:
        def __init__(self, method_owner, tracepoint_: "_TracepointSync"):
            self._owner = method_owner
            self._tp = tracepoint_

        async def __call__(self, *args, **kwargs) -> _T:
            return await self._tp(self._owner, *args, **kwargs)

        async def with_trace(self, *args, **kwargs) -> _T:
            return await self._tp.with_trace(self._owner, *args, **kwargs)

    async def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        input_dict = _make_traced_input(self.__signature__, args, kwargs, self._invisible_args, self._arg_replacement)

        async def runner(inputs: dict):
            _ = inputs
            return await self._f(*args, **kwargs)

        runner: Callable  # override for type hint
        return await RunnableLambda(runner, name=self._name).ainvoke(input_dict)

    async def with_trace(self, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        with set_config_context(RunnableConfig(callbacks=_get_callbacks())) as ctx:
            return await coro_with_context(self(*args, **kwargs), ctx, create_task=True)


@overload  # This overload is for PyCharm type hint. Same usage as below.
def tracepoint(f: _Func) -> _TracedFunc[_Func] | _Func:
    """Create as a decorator without args."""


@overload
def tracepoint(f: Callable[_P, _T]) -> _Tracepoint[_P, _T]:
    """
    Create as a decorator without args.

    Examples:
        >>> @tracepoint
        ... def my_task(a: int, b: str) -> str:
        ...    return b * a
        >>> my_task(1, "b")
    """


@overload  # This overload is for PyCharm type hint. Same usage as below.
def tracepoint(display_name: str = None,
               invisible_args: Iterable[str] | str = frozenset(),
               arg_replacement: Mapping[str, Callable[[Any], Any]] = None,
               is_async: bool = "auto",
               **arg_replace_func: Callable[[Any], Any]) -> Callable[[_Func], _TracedFunc[_Func] | _Func]:
    """Create as a decorator with args."""


@overload
def tracepoint(display_name: str = None,
               invisible_args: Iterable[str] | str = frozenset(),
               arg_replacement: Mapping[str, Callable[[Any], Any]] = None,
               is_async: bool = "auto",
               **arg_replace_func: Callable[[Any], Any]) -> Callable[[Callable[_P, _T]], _Tracepoint[_P, _T]]:
    """
    Create as a decorator with args.

    Examples:
        >>> @tracepoint("my_tracepoint")
        ... def my_task(a: int, b: str) -> str:
        ...    return b * a
        >>> my_task(10, "b")
    """


@overload  # This overload is for PyCharm type hint. Same usage as below.
def tracepoint(f: _Func, /,
               display_name: str = None,
               invisible_args: Iterable[str] | str = frozenset(),
               arg_replacement: Mapping[str, Callable[[Any], Any]] = None,
               is_async: bool = "auto",
               **arg_replace_func: Callable[[Any], Any]) -> _TracedFunc[_Func] | _Func:
    """Create as a constructor."""


@overload
def tracepoint(f: Callable[_P, _T], /,
               display_name: str = None,
               invisible_args: Iterable[str] | str = frozenset(),
               arg_replacement: Mapping[str, Callable[[Any], Any]] = None,
               is_async: bool = "auto",
               **arg_replace_func: Callable[[Any], Any]) -> _Tracepoint[_P, _T]:
    """
    Create as a constructor.

    Examples:
        >>> my_task = tracepoint(lambda x, y: y * x, display_name="my_tracepoint")
        >>> my_task(3, y="Y")
    """


@no_type_check
def tracepoint(f_or_name: Callable[_P, _T] | str = None, /,
               display_name: str = None,
               invisible_args: Iterable[str] | str = frozenset(),
               arg_replacement: Mapping[str, Callable[[Any], Any]] = None,
               is_async: bool = "auto",
               **arg_replace_func: Callable[[Any], Any]) -> _Tracepoint[_P, _T] | Callable[
    [Callable[_P, _T]], _Tracepoint[_P, _T]]:
    if callable(f_or_name):
        f = f_or_name
        if isinstance(invisible_args, str):
            invisible_args: frozenset[str] = frozenset([invisible_args])
        else:
            invisible_args: frozenset[str] = frozenset(invisible_args)
        arg_replacement = dict(arg_replacement) if arg_replacement else {}
        arg_replacement.update(arg_replace_func)
        sig = inspect.signature(f)
        display_name = _check_trace_config(f, sig, display_name, invisible_args, arg_replacement)
        return _create_stub(f, display_name, invisible_args, arg_replacement, is_async)
    return functools.partial(tracepoint, display_name=f_or_name or display_name, invisible_args=invisible_args,
                             arg_replacement=arg_replacement, **arg_replace_func)


def _check_trace_config(func: Callable, sig: inspect.Signature, display_name: str,
                        invisible_args: frozenset[str], arg_replacement: Mapping[str, Callable[[Any], Any]]) -> str:
    func_name = getattr(func, "__qualname__", None) or getattr(func, "__name__", type(func).__name__)
    actual_params = set(sig.parameters.keys())
    replace_set = set(arg_replacement)

    conflict = invisible_args.intersection(replace_set)
    extra = invisible_args.union(replace_set) - actual_params
    if conflict or extra:
        err_msg = f"Signature check for {func_name!r} failed."
        if conflict:
            err_msg += f" Both {', '.join(repr(s) for s in conflict)} are in 'invisible_args' and 'arg_replacement'."
        if extra:
            err_msg += (f" {'And these' if conflict else 'These'} are not argument names of this callable: "
                        f"{', '.join(repr(s) for s in extra)}.")
        raise TypeError(err_msg)

    return str(display_name) if display_name else func_name


def _make_traced_input(sig: inspect.Signature, args: tuple, kwargs: dict,
                       invisible_args: frozenset[str], arg_replacement: Mapping[str, Callable[[Any], Any]]) -> dict:
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    arg_dict = dict(bound.arguments)
    for arg_name in invisible_args:
        arg_dict.pop(arg_name, None)
    for arg_name, handler in arg_replacement.items():
        if arg_name in arg_dict:
            arg_dict[arg_name] = handler(arg_dict[arg_name])
    return arg_dict


def _create_stub(f: Callable[_P, _T], display_name: str,
                 invisible_args: frozenset[str], arg_replacement: Mapping[str, Callable[[Any], Any]],
                 is_async: bool | Literal["auto"]) -> Callable[_P, _T]:
    if is_async == "auto":
        is_async = asyncio.iscoroutinefunction(f)
    if is_async:
        return _TracepointAsync(f, display_name, invisible_args, arg_replacement)
    return _TracepointSync(f, display_name, invisible_args, arg_replacement)


class _TraceContextMgr:
    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks: list[BaseCallbackHandler] | None = None

    @staticmethod
    def _make_langfuse() -> BaseCallbackHandler | None:
        if "LANGFUSE_PUBLIC_KEY" in os.environ:
            logger.info("Langfuse environ variable detected. Try creating its client.")
            from langfuse.langchain import CallbackHandler as LangfuseCallback
            return LangfuseCallback()
        return None

    def make_global_callbacks(self) -> list[BaseCallbackHandler]:
        if self._callbacks is not None:
            return self._callbacks
        with self._lock:
            if self._callbacks is not None:
                return self._callbacks
            callbacks = [
                f() for f in (self._make_langfuse,)
            ]
            self._callbacks = [c for c in callbacks if c is not None]
        return self._callbacks


_get_callbacks = _TraceContextMgr().make_global_callbacks
