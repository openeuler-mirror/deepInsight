import os
import time
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from langgraph.func import entrypoint, task

from deepinsight.utils.trace_utils import tracepoint


def _target_function(arg1, arg2, *args, kw1, kw2="2", **kwargs):
    pass


def _target_async(arg1, arg2, *args, kw1, kw2="2", **kwargs):
    pass


class _TargetClass:
    def my_method(self, arg1, arg2, *args, kw1, kw2="2", **kwargs):
        pass

    def __call__(self, arg1, arg2, *args, kw1, kw2="2", **kwargs):
        pass


def _replace_to_none(_):
    return None


class TestTraceUtils(IsolatedAsyncioTestCase):
    def test_signature_check(self):
        obj = _TargetClass()
        # nothing: ok
        tracepoint(_target_function)
        tracepoint(obj)
        tracepoint(obj.my_method)

        # names exists: ok
        tracepoint(_target_function, invisible_args=["arg1"], kw2=_replace_to_none)
        tracepoint(obj, invisible_args=["arg1"], kw2=_replace_to_none)
        tracepoint(obj.my_method, invisible_args=["arg1"], kw2=_replace_to_none)
        # 'self' is bound and should not be args
        self.assertRaisesRegex(TypeError, "These are not argument names of this callable: 'self'.",
                               lambda: tracepoint(obj, invisible_args=["self"]))
        self.assertRaisesRegex(TypeError, "These are not argument names of this callable: 'self'.",
                               lambda: tracepoint(obj.my_method, invisible_args=["self"]))

    @patch.dict(os.environ, clear=True)
    def test_decorator(self):
        @tracepoint(display_name="test1", invisible_args="x1")
        def target(x1=2):
            return x1 * 2

        self.assertEqual(4, target())
        self.assertEqual(6, target(x1=3))
        self.assertEqual(8, target(4))
        self.assertEqual(4, target.with_trace())
        self.assertEqual(6, target.with_trace(x1=3))
        self.assertEqual(8, target.with_trace(4))

    @patch.dict(os.environ, clear=True)
    async def test_decorator_async(self):
        @tracepoint(display_name="test2", invisible_args="x1")
        async def target2(x1=2):
            return x1 * 2

        self.assertEqual(4, await target2())
        self.assertEqual(6, await target2(x1=3))
        self.assertEqual(8, await target2(4))
        self.assertEqual(4, await target2.with_trace())
        self.assertEqual(6, await target2.with_trace(x1=3))
        self.assertEqual(8, await target2.with_trace(4))

    @patch.dict(os.environ, clear=True)
    async def test_method(self):
        class Target:
            @tracepoint(invisible_args="self")
            async def method(self, y2):
                return y2 ** 2

            @tracepoint(invisible_args="self")
            async def __call__(self, z):
                return z ** 3

        self.assertEqual(9, await Target().method(y2=3))
        self.assertEqual(16, await Target().method(4))

        self.assertEqual(27, await Target()(z=3))
        self.assertEqual(64, await Target()(4))

        self.assertEqual(9, await Target().method.with_trace(y2=3))
        self.assertEqual(16, await Target().method.with_trace(4))
        # object.__call__ cannot apply `with_trace`: no such attribute

    @patch.dict(os.environ, clear=True)
    def test_performance(self):
        @task
        def inner1(a: str, b: int, c: str) -> str:
            return a * b + c

        @entrypoint()
        def caller1(inputs: dict):
            return ",".join([inner1(**inputs).result() for _ in range(100)])

        @tracepoint
        def inner2(a: str, b: int, c: str) -> str:
            return a * b + c

        @tracepoint
        def caller2(x: str, y: int, z: str) -> str:
            return ",".join([inner2(x, y, z) for _ in range(100)])

        count = 1_000
        # warmup
        self.assertEqual(
            caller1.invoke(dict(a="1", b=2, c="3")),  # noqa
            caller2.with_trace(x="1", y=2, z="3")
        )
        # run
        start = time.time()
        for _ in range(count):
            caller1.invoke(dict(a="1", b=2, c="3"))  # noqa
        mid = time.time()
        for _ in range(count):
            caller2.with_trace(x="1", y=2, z="3")
        end = time.time()
        ref = mid - start
        target = end - mid
        self.assertLess(target / ref, 0.35,  # currently is about 25%
                        f"Time cose is {target:3.1f}s while LangGraph takes {ref:3.1f}s for {count} times")
