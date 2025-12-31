"""Test deepinsight.utils.singleton."""
from unittest import TestCase
from unittest.mock import patch

from deepinsight.utils.singleton import SingletonMeta


_some_side_effects = []


class ReferencedClass(metaclass=SingletonMeta):
    def __init__(self):
        self.len = len(_some_side_effects)
        _some_side_effects.append(self.len)

class RefererClass(metaclass=SingletonMeta):
    def __init__(self):
        self.y = ReferencedClass()
        self.len = len(_some_side_effects)
        _some_side_effects.append(self.len)


class TestSingleton(TestCase):
    def setUp(self):
        _some_side_effects.clear()

    @patch.dict("deepinsight.utils.singleton._instances", clear=True)
    @patch.dict("deepinsight.utils.singleton._init_locks", clear=True)
    def test_singleton(self):
        a = ReferencedClass()
        b = ReferencedClass()
        self.assertIs(a, b)
        self.assertEqual(_some_side_effects, [0])

    @patch.dict("deepinsight.utils.singleton._instances", clear=True)
    @patch.dict("deepinsight.utils.singleton._init_locks", clear=True)
    def test_nesting(self):
        a = RefererClass()
        b = RefererClass()
        c = ReferencedClass()
        self.assertIs(a, b)
        self.assertIs(a.y, c)
        self.assertEqual(set(_some_side_effects), {0, 1})

    def tearDown(self):
        _some_side_effects.clear()
