from os import environ
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pydantic import ValidationError

from deepinsight.service.rag.loaders.mineru_offline import MinerUOfflineClient, RunMode

_LOCALHOST = "http://localhost"
_ANOTHER_URL = "http://127.0.0.1"
_PYDANTIC_NOT_URL_MSG = "Input should be a valid URL"


class TestMineruOfflineClient(IsolatedAsyncioTestCase):
    def test_base_url_validator(self):
        with patch.dict(environ, clear=True):
            self.assertRaisesRegexp(
                ValueError,
                "Pass base_url to client or setting 'MINERU_OFFLINE_BASE_URL' environment variable.",
                MinerUOfflineClient
            )
        with patch.dict(environ, values={"MINERU_OFFLINE_BASE_URL": "1"}, clear=True):
            self.assertRaisesRegexp(ValueError,
                                    "Environment variable 'MINERU_OFFLINE_BASE_URL' is not a valid URL",
                                    MinerUOfflineClient)
        with patch.dict(environ, values={"MINERU_OFFLINE_BASE_URL": _LOCALHOST}, clear=True):
            url = MinerUOfflineClient().base_url
            self.assertIsInstance(url, str)
            self.assertEqual(url, _LOCALHOST)

            url = MinerUOfflineClient(base_url=_ANOTHER_URL).base_url
            self.assertIsInstance(url, str)
            self.assertEqual(url, _ANOTHER_URL)

            with self.assertRaisesRegexp(ValidationError, _PYDANTIC_NOT_URL_MSG):
                MinerUOfflineClient(base_url="1")

    @patch.dict(environ, values={"MINERU_OFFLINE_BASE_URL": _LOCALHOST}, clear=True)
    def test_run_mode_validator(self):
        with patch.dict(environ, values={"MINERU_OFFLINE_MODE": ""}):
            MinerUOfflineClient()

        with (
            patch.dict(environ, values={"MINERU_OFFLINE_MODE": "1"}),
            self.assertRaisesRegexp(ValueError, "Environment variable 'MINERU_OFFLINE_MODE' can only in ")
        ):
            MinerUOfflineClient()

        with patch.dict(environ, values={"MINERU_OFFLINE_MODE": "json"}):
            mode = MinerUOfflineClient().run_mode
            self.assertIsInstance(mode, RunMode)
            self.assertEqual(mode, RunMode.JSON)

            mode = MinerUOfflineClient(run_mode=RunMode.ZIP).run_mode
            self.assertIsInstance(mode, RunMode)
            self.assertEqual(mode, RunMode.ZIP)

            with self.assertRaises(ValidationError):
                MinerUOfflineClient(run_mode="test")  # type: ignore

    @patch.dict(environ, values={"MINERU_OFFLINE_BASE_URL": _LOCALHOST}, clear=True)
    def test_max_process_time_from_env(self):
        client = MinerUOfflineClient()
        self.assertEqual(client.max_process_time, 3600.0)

        for env, val in (("120.5", 120.5), ("180", 180.0), ("", 3600.0), ("1.5e3", 1500.0)):
            with patch.dict(environ, values={"MINERU_OFFLINE_MAX_TIMEOUT": env}):
                client = MinerUOfflineClient()
                self.assertIsInstance(client.max_process_time, float)
                self.assertEqual(client.max_process_time, val)

        for val in ("0", "-1", "test", "1..2"):
            with (
                patch.dict(environ, values={"MINERU_OFFLINE_MAX_TIMEOUT": val}),
                self.assertRaisesRegexp(ValueError, f"can only be a float greater than zero, but got {val!r}")
            ):
                MinerUOfflineClient()

    @patch.dict(environ, values={
        "MINERU_OFFLINE_BASE_URL": _LOCALHOST,
        "MINERU_OFFLINE_MAX_TIMEOUT": "1"
    }, clear=True)
    def test_max_process_time_constructor(self):
        client = MinerUOfflineClient(max_process_time=200.0)
        self.assertEqual(client.max_process_time, 200.0)

        for val in (0, -1.0, "test"):
            with self.assertRaises(ValidationError):
                MinerUOfflineClient(max_process_time=val)