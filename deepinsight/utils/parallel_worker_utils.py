# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import inspect
from concurrent.futures import ThreadPoolExecutor, Future
import logging
from os import environ, cpu_count
from typing import Callable, Generator, Iterable, Optional, TypeVar, overload


_S = TypeVar('_S')
_T = TypeVar('_T')
_DEFAULT_NUM_PROCS = int(environ.get('MAX_EXECUTOR_WORKERS', '0')) or (cpu_count() * 5)


class Executor:
    """A parallel task executor that handles both regular functions and generators."""

    def __init__(self, name: str, num_procs=_DEFAULT_NUM_PROCS):
        """
        Initialize the executor with a name and worker count.

        Args:
            name: Descriptive name for the executor (used in thread names)
            num_procs: Number of worker processes (defaults to CPU count * 5)
        """
        self.__name = name
        prefix = ''.join(w.capitalize() for w in name.split())
        self.__executor = ThreadPoolExecutor(num_procs, thread_name_prefix=f"{prefix}Worker")

    @staticmethod
    def __is_generator_function(f: Callable) -> bool:
        """
        Check if a callable is a generator function.

        Handles special cases like beartype-wrapped functions.

        Args:
            f: Callable to check

        Returns:
            bool: True if the callable is a generator function
        """
        # handle beartype
        if f.__dict__.get('__beartype_wrapper') is True:
            if inspect.isfunction(f.__dict__.get('__wrapped__')):
                f = f.__wrapped__  # noqa
        return inspect.isgeneratorfunction(f)

    @overload
    def __call__(self,
                 func: Callable[..., Generator[_S, None, _T]],
                 workloads: list[Iterable]) -> Generator[_S, None, list[_T]]:
        """Overload for generator functions."""
        ...

    @overload
    def __call__(self,
                 func: Callable[..., _T],
                 workloads: list[Iterable]) -> list[_T]:
        """Overload for generator functions."""
        ...

    def __call__(self,
                 func: Callable,
                 workloads: list[Iterable]):
        """
        Execute function across multiple workloads in parallel.

        Automatically handles both generator and non-generator functions.

        Args:
            func: Function to execute (can be generator or regular function)
            workloads: List of argument iterables for each task

        Returns:
            For generators: yields intermediate results and returns final results
            For regular functions: returns list of results
        """
        if self.__is_generator_function(func):
            return self.__do_generator_call(func, workloads)
        return self.__do_no_generator_call(func, workloads)

    def __do_no_generator_call(self,
                               func: Callable[..., _T],
                               workloads: list[Iterable]) -> list[_T]:
        """
        Execute a regular function across workloads.

        Args:
            func: Regular function to execute
            workloads: List of argument iterables

        Returns:
            list[_T]: List of results from each workload
        """
        if not workloads:
            return []
        if len(workloads) == 1:
            return [func(*workloads[0])]
        return list(self.__executor.map(lambda arg: func(*arg), workloads))

    def __do_generator_call(self,
                            func: Callable[..., Generator[_S, None, _T]],
                            workloads: list[Iterable]) -> Generator[_S, None, list[_T]]:
        """
        Execute a generator function across workloads with proper error handling.

        Args:
            func: Generator function to execute
            workloads: List of argument iterables

        Yields:
            _S: Intermediate results from generators

        Returns:
            list[_T]: Final results from all workloads

        Raises:
            Exception: If any workload fails
        """
        if not workloads:
            return []
        futures: list[Future[tuple[list[_S], _T, Optional[Exception]]]] = [
            self.__executor.submit(_generator_worker, func, workload)
            for workload in workloads[1:]
        ]
        logging.info(f"Submitted {len(futures)} of {len(workloads)} {self.__name} sub tasks."
                     " Begin executing the first workload.")
        try:
            ret0 = yield from func(*workloads[0])
        except Exception:
            logging.error(f"First {self.__name} workload failed. Cancel the other workloads.")
            for f in futures:
                f.cancel()
            raise
        ret = [ret0]
        logging.info(f"Execution of first {self.__name} workload finished. Collect the other {len(futures)} workloads.")
        e = None
        for i, f in enumerate(futures):
            if e:
                f.cancel()
                continue
            it, result, e = f.result()
            yield from it
            if e:
                logging.error(f"{self.__name} workloads[{i + 1}] failed, cancel the remaining tasks."
                              f" {type(e).__name__}: {e}")
                raise e
            ret.append(result)
        return ret


def _generator_worker(
        func: Callable[..., Generator[_S, None, _T]],
        workload: Iterable) -> tuple[list[_S], _T, Optional[Exception]]:
    """
    Worker function for executing generator functions in threads.

    Args:
        func: Generator function to execute
        workload: Arguments for the function

    Returns:
        tuple: Contains:
            - list of yielded values
            - final return value
            - exception if one occurred
    """
    yields = []
    it = func(*workload)
    try:
        while True:
            yields.append(next(it))
    except StopIteration as e:
        return yields, e.value, None
    except Exception as e1:
        return yields, None, e1