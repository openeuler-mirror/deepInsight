"""Test"""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from deepinsight.config.database_config import DatabaseConfig
from deepinsight.config.config import Config
from deepinsight.databases.connection import Database
from deepinsight.databases.models.base import Base as BaseTable
from deepinsight.service.conference.conference import ConferenceService


class TestConfDbOperation(IsolatedAsyncioTestCase):
    memory_db_config = DatabaseConfig(url="sqlite:///:memory:?cache=shared")

    def tearDown(self):
        Database._instance = None  # noqa: cleanup memory database for next test

    @patch("deepinsight.service.conference.conference.KnowledgeService")
    @patch("deepinsight.service.conference.conference.PaperExtractionService")
    async def test_create_conference_conflict(self, *_mock):
        """Testcase for a parallel parse request to a same conference.

        This often occurs when using external knowledge base services and starting parsing in batches。
        """
        target_short = "MY-CONF"
        year1 = 2025
        year2 = 2026
        target_full = "My Conference @ {}"
        target_topic = ["topic1", "topic2"]
        target_website = "http://localhost/my/example"

        config: Config = MagicMock()
        config.database = self.memory_db_config

        service = ConferenceService(config)
        BaseTable.metadata.create_all(Database().engine)

        async def mocked_query_conf_meta(short_name, year):
            """Sleep to inject conflict."""
            self.assertEqual(target_short, short_name)
            await asyncio.sleep(2)
            return ConferenceService.Conference(
                full_name=target_full.format(year),
                website=target_website,
                topics=target_topic
            )

        mocked_protected = MagicMock()
        mocked_protected.side_effect = mocked_query_conf_meta
        service._query_conference_meta = mocked_protected  # noqa: for mock

        output = await asyncio.gather(
            *[service.get_or_create_conference(target_short, year1) for _ in range(3)],  # ID 1
            *[service.get_or_create_conference(target_short, year2) for _ in range(4)]  # ID 2
        )
        id_list = [i[0] for i in output]
        name_list = [i[1] for i in output]
        id1, id2 = set(id_list[:3]), set(id_list[3:])
        self.assertFalse(id1 == id2)
        self.assertEqual(1, len(id1))
        self.assertEqual(1, len(id2))
        self.assertEqual([target_full.format(2025)] * 3 + [target_full.format(year2)] * 4, name_list)

        self.assertIn((await service.get_or_create_conference(target_short, year1))[0], id1)
        self.assertIn((await service.get_or_create_conference(target_short, year2))[0], id2)

        self.assertEqual(7, mocked_protected.call_count)
