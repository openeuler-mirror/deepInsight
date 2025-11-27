from os import path
from unittest import IsolatedAsyncioTestCase

from deepinsight.config.file_storage_config import ObsMappingConfig, MappingItem
from deepinsight.utils.file_storage.local import LocalStorage
from deepinsight.utils.file_storage import StorageError


FOR_TEST_CONFIG = ObsMappingConfig(
    kb_doc_image=MappingItem(bucket="aaa{kb_id}", object="bbb/{doc_id}/{img_path}")
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.path, ignore_errors=True)
