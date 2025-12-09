import logging
import os.path
from unittest import IsolatedAsyncioTestCase

import boto3

from deepinsight.config.file_storage_config import ConfigS3
from deepinsight.utils.file_storage import StorageOp, StorageError
from deepinsight.utils.file_storage.s3_compatible import S3CompatibleObsClient


class TestStorageS3(IsolatedAsyncioTestCase):
    async def test_storage(self):
        endpoint = os.getenv("ST_OBS_S3_ENDPOINT")
        ak = os.getenv("ST_OBS_S3_AK")
        sk = os.getenv("ST_OBS_S3_SK")
        bucket = os.getenv("ST_OBS_S3_BUCKET1")
        fake_bucket = os.getenv("ST_OBS_S3_BUCKET2")

        if not all((endpoint, ak, sk, bucket, fake_bucket)):
            self.skipTest("No available S3 compatible endpoint. Set 'ST_OBS_S3_ENDPOINT', 'ST_OBS_S3_AK', "
                          "'ST_OBS_S3_SK', 'ST_OBS_S3_BUCKET1', 'ST_OBS_S3_BUCKET2' to test this case.")

        async with S3CompatibleObsClient(config=ConfigS3(endpoint=endpoint, ak=ak, sk=sk)) as storage: # type: ignore
            file1 = "100%20.txt"
            file2 = "1/中文~ 带空格.txt"
            fake_file = "1"

            content1 = b"123"
            content2 = b"12345"

            r = StorageError.Reason
            already_exists = set(await storage.list_buckets())
            await storage.bucket_create(bucket, exist_ok=False)
            self.assertEqual({*already_exists, bucket}, set(await storage.list_buckets()))
            await storage.bucket_create(bucket, exist_ok=True)
            self.assertEqual({*already_exists, bucket}, set(await storage.list_buckets()))

            await self._assert_raises(storage.file_add(fake_bucket, file1, content1),
                                      StorageOp.CREATE, fake_bucket, file1, r.BUCKET_NOT_FOUND)

            await storage.file_add(bucket, file1, content1)
            self.assertEqual([file1], await storage.list_files(bucket))
            self.assertEqual([], await storage.list_files(bucket, "2"))

            await storage.file_add(bucket, file2, content2)
            self.assertEqual([file2], await storage.list_files(bucket, "1/"))
            self.assertEqual({file1, file2}, set(await storage.list_files(bucket)))
            await self._assert_raises(storage.file_get(bucket, fake_file),
                                      StorageOp.GET, bucket, fake_file, r.FILE_NOT_FOUND)

            self.assertEqual(content1, await storage.file_get(bucket, file1))
            self.assertEqual(content2, await storage.file_get(bucket, file2))
            await self._assert_raises(storage.file_get(fake_bucket, file2),
                                      StorageOp.GET, fake_bucket, file2, r.FILE_NOT_FOUND)

            await storage.file_delete(bucket, file2)
            await storage.file_delete(bucket, file2, allow_not_exists=True)
            await storage.file_delete(bucket, file2, allow_not_exists=False)  # always allow
            self.assertEqual([file1], await storage.list_files(bucket))

    def tearDown(self):
        endpoint = os.getenv("ST_OBS_S3_ENDPOINT")
        ak = os.getenv("ST_OBS_S3_AK")
        sk = os.getenv("ST_OBS_S3_SK")
        bucket_name = os.getenv("ST_OBS_S3_BUCKET1")
        if not all((endpoint, ak, sk, bucket_name)):
            return
        try:
            s3 = boto3.resource("s3", endpoint_url=endpoint, aws_access_key_id=ak, aws_secret_access_key=sk)
            bucket = s3.Bucket(bucket_name)
            bucket.objects.all().delete()
            bucket.delete()
        except Exception as e:
            logging.warning(f"Cleanup failed with {e}")

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
