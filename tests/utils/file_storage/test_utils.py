from os import path
from unittest import IsolatedAsyncioTestCase

from deepinsight.config.file_storage_config import ObsMappingConfig, MappingItem
from deepinsight.utils.file_storage.local import LocalStorage


FOR_TEST_CONFIG = ObsMappingConfig(
    kb_doc_image=MappingItem(bucket="aaa{kb_id}", object="bbb/{doc_id}/{img_path}"),
    kb_doc_binary=MappingItem(bucket="bbb{kb_id}", object="ccc/{kb_id}/{doc_id}/{doc_name}"),
    report_image=MappingItem(bucket="report-img-bucket-test", object="some/of/the/{img_path}")
)


class TestUtilFuncs(IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = path.join(path.dirname(path.abspath(__file__)), "./ut_file_storage_utils")
        self.assertFalse(path.exists(self.path))
        self.storage = LocalStorage(root_dir=self.path, keymap=FOR_TEST_CONFIG)

    async def test_document_images(self):
        self.assertEqual([], await self.storage.list_buckets())
        kb_id = "_x"
        bucket = "aaa_x"
        await self.storage.document_images_init_bucket(kb_id)
        self.assertEqual([bucket], await self.storage.list_buckets())
        images = {f"some/{i}.jpg": (f"{i}" * i).encode("utf8") for i in range(3, 6)}
        doc_id = "test1"
        await self.storage.document_images_store(kb_id, doc_id, images)
        actual = set(await self.storage.list_files(bucket))
        want = {f"bbb/{doc_id}/{img}" for img in images}
        self.assertEqual(want, actual)
        for name, content in images.items():
            self.assertEqual(content, await self.storage.file_get(bucket, f"bbb/{doc_id}/{name}"))

    async def test_document_binary(self):
        self.assertEqual([], await self.storage.list_buckets())
        kb_id = "_x"
        bucket = "bbb_x"
        owner = "unused"
        owner_id = "unused_id"
        await self.storage.knowledge_file_init_bucket(kb_id, owner, owner_id)
        self.assertEqual([bucket], await self.storage.list_buckets())
        docs = [
            (f"some_{i}.pdf", str(i), (f"{i}1" * i).encode("utf8"))
            for i in range(4, 7)
        ]
        for name, doc_id, content in docs:
            await self.storage.knowledge_file_put(kb_id, owner, owner_id, doc_id, name, content)
        actual = set(await self.storage.list_files(bucket))
        want = {f"ccc/{kb_id}/{doc_id}/{name}" for name, doc_id, _ in docs}
        self.assertEqual(want, actual)
        for name, doc_id, content in docs:
            self.assertEqual(content, await self.storage.knowledge_file_get(kb_id, owner, owner_id, doc_id, name))
            self.assertEqual(content, await self.storage.file_get(bucket, f"ccc/{kb_id}/{doc_id}/{name}"))

    async def test_chart_images(self):
        self.assertEqual([], await self.storage.list_buckets())
        bucket = "report-img-bucket-test"
        images = {f"some/{i}.png": (f"{i}" * i).encode("utf8") for i in range(10, 13)}
        for name, content in images.items():
            await self.storage.chart_store(name, content)
        self.assertEqual([bucket], await self.storage.list_buckets())

        actual = set(await self.storage.list_files(bucket))
        want = {f"some/of/the/{img}" for img in images}
        self.assertEqual(want, actual)
        for name, content in images.items():
            self.assertEqual(content, await self.storage.file_get(bucket, f"some/of/the/{name}"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.path, ignore_errors=True)
