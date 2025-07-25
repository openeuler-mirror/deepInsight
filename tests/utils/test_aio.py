# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import asyncio
from unittest import TestCase

from deepinsight.utils.aio import get_or_create_loop


class Test(TestCase):
    def setUp(self):
        """
        Setup method run before each test case.
        Saves the original event loop (if any exists) and ensures a clean state
        by setting the event loop to None before each test.
        """
        # Reset event loop state before each test
        self._original_loop = None
        try:
            self._original_loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        asyncio.set_event_loop(None)

    def tearDown(self):
        """
        Cleanup method run after each test case.
        Closes any created event loop and restores the original event loop state.
        """
        # Clean up any created loop and restore original state
        try:
            loop = asyncio.get_event_loop()
            if loop is not None and not loop.is_closed():
                loop.close()
        except RuntimeError:
            pass
        asyncio.set_event_loop(None)
        if self._original_loop is not None:
            asyncio.set_event_loop(self._original_loop)

    def test_get_existing_loop(self):
        """
        Test case for get_or_create_loop() when an event loop already exists.
        Verifies that the function returns the existing event loop.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = get_or_create_loop()
            self.assertIs(loop, result)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_create_new_loop(self):
        """
           Test case for get_or_create_loop() when no event loop exists.
           Verifies that the function creates and returns a new event loop.
        """
        # Ensure no loop exists
        asyncio.set_event_loop(None)

        result = get_or_create_loop()
        try:
            self.assertIsInstance(result, asyncio.AbstractEventLoop)
            current_loop = asyncio.get_event_loop()
            self.assertIs(result, current_loop)
        finally:
            result.close()
            asyncio.set_event_loop(None)

    def test_set_new_loop(self):
        """
        Test case verifying that get_or_create_loop() sets the new loop as current.
        """
        # Ensure no loop exists
        asyncio.set_event_loop(None)

        loop = get_or_create_loop()
        try:
            current_loop = asyncio.get_event_loop()
            self.assertIs(loop, current_loop)
        finally:
            loop.close()
            asyncio.set_event_loop(None)
