import asyncio
import os.path
from unittest import TestCase
from unittest.mock import MagicMock

from deepinsight.config.config import Config
from deepinsight.config.file_storage_config import FileStorageConfig
from deepinsight.utils.file_storage import get_storage_impl, StorageOp, StorageError


class TestStorageLocal(TestCase):
    target_dir = "./local_storage_test"

    def setUp(self):
        self.assertFalse(os.path.exists(self.target_dir))

    def test_storage(self):
        config: Config = MagicMock()
        config.workspace.work_root = self.target_dir
        config.file_storage = FileStorageConfig(type="local")  # type: ignore
        get_storage_impl(config)
        self.assertTrue(os.path.exists(self.target_dir))

        async def test_main():
            storage = get_storage_impl()
            bucket = "ab"
            fake_bucket = "abb"

            file1 = "1.txt"
            file2 = "1/1.txt"
            fake_file = "1"

            content1 = b"123"
            content2 = b"12345"

            r = StorageError.Reason

            self.assertEqual([], await storage.list_buckets())
            await storage.bucket_create(bucket, exist_ok=True)
            self.assertEqual([bucket], await storage.list_buckets())

            await self._assert_raises(storage.file_add(fake_bucket, file1, content1),
                                      StorageOp.CREATE, fake_bucket, file1, r.BUCKET_NOT_FOUND)

            await storage.file_add(bucket, file1, content1)
            self.assertEqual([file1], await storage.list_files(bucket))
            self.assertEqual([], await storage.list_files(bucket, "1"))

            await storage.file_add(bucket, file2, content2)
            self.assertEqual([file2], await storage.list_files(bucket, "1"))
            self.assertEqual({file1, file2}, set(await storage.list_files(bucket)))
            await self._assert_raises(storage.file_get(bucket, fake_file),
                                      StorageOp.GET, bucket, fake_file, r.FILE_NOT_FOUND)

            self.assertEqual(content1, await storage.file_get(bucket, file1))
            self.assertEqual(content2, await storage.file_get(bucket, file2))
            await self._assert_raises(storage.file_get(fake_bucket, file2),
                                      StorageOp.GET, fake_bucket, file2, r.BUCKET_NOT_FOUND)

            await storage.file_delete(bucket, file2, allow_not_exists=False)
            await storage.file_delete(bucket, file2, allow_not_exists=True)
            await self._assert_raises(storage.file_delete(bucket, file2, allow_not_exists=False),
                                      StorageOp.DELETE, bucket, file2, r.FILE_NOT_FOUND)
            self.assertEqual([file1], await storage.list_files(bucket))

        asyncio.run(test_main())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.target_dir, ignore_errors=True)

    async def _assert_raises(self, awaitable, op: StorageOp, bucket: str, file: str, reason):
        try:
            await awaitable
        except StorageError as e:
            self.assertEqual(e.op, op)
            self.assertEqual(e.bucket, bucket)
            self.assertEqual(e.filename, file)
            self.assertEqual(e.reason, reason)
        else:
            self.fail("Except raises")
