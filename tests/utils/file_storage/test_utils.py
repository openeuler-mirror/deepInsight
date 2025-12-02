from os import path, environ
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from deepinsight.utils.file_storage.local import LocalStorage
from deepinsight.utils.file_storage.identify import KbDocImage, KbDocBinary, ReportImage


class TestUtilFuncs(IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = path.join(path.dirname(path.abspath(__file__)), "./ut_file_storage_utils")
        self.assertFalse(path.exists(self.path))
        self.patch_dict = dict(
            DEEPINSIGHT_OBS_KB_DOC_IMG_BUCKET="aaa{kb_id}",
            DEEPINSIGHT_OBS_KB_DOC_IMG_OBJECT="bbb/{doc_id}",
            DEEPINSIGHT_OBS_KB_DOC_BINARY_BUCKET="bbb{kb_id}",
            DEEPINSIGHT_OBS_KB_DOC_BINARY_OBJECT="ccc/{kb_id}/{doc_id}/{doc_name}",
            DEEPINSIGHT_OBS_REPORT_IMG_BUCKET="report-img-bucket-test",
            DEEPINSIGHT_OBS_REPORT_IMG_OBJECT="some/of/the/{img_path}",
        )
        self.patch = patch.dict(environ, self.patch_dict, clear=True)
        self.patch.start()
        self.storage = LocalStorage(root_dir=self.path)

    async def test_document_images(self):
        self.assertEqual([], await self.storage.list_buckets())
        kb_id = "_x"
        bucket = "aaa_x"
        await self.storage.object_init_bucket(KbDocImage(kb_id=kb_id))
        self.assertEqual([bucket], await self.storage.list_buckets())
        images = {f"some/{i}.jpg": (f"{i}" * i).encode("utf8") for i in range(3, 6)}
        doc_id = "test1"
        await self.storage.object_put(KbDocImage(kb_id=kb_id, doc_id=doc_id), images)
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
        await self.storage.object_init_bucket(KbDocBinary(kb_id=kb_id, owner_type=owner, owner_id=owner_id))
        self.assertEqual([bucket], await self.storage.list_buckets())
        docs = [
            (f"some_{i}.pdf", str(i), (f"{i}1" * i).encode("utf8"))
            for i in range(4, 7)
        ]
        for name, doc_id, content in docs:
            await self.storage.object_put(
                KbDocBinary(kb_id=kb_id, owner_type=owner, owner_id=owner_id, doc_id=doc_id, doc_name=name), content)
        actual = set(await self.storage.list_files(bucket))
        want = {f"ccc/{kb_id}/{doc_id}/{name}" for name, doc_id, _ in docs}
        self.assertEqual(want, actual)
        for name, doc_id, content in docs:
            self.assertEqual(content, await self.storage.object_get(
                KbDocBinary(kb_id=kb_id, owner_type=owner, owner_id=owner_id, doc_id=doc_id, doc_name=name)))
            self.assertEqual(content, await self.storage.file_get(bucket, f"ccc/{kb_id}/{doc_id}/{name}"))

    async def test_chart_images(self):
        self.assertEqual([], await self.storage.list_buckets())
        bucket = "report-img-bucket-test"
        images = {f"some/{i}.png": (f"{i}" * i).encode("utf8") for i in range(10, 13)}
        for name, content in images.items():
            await self.storage.object_put(ReportImage(img_path=name), content, auto_create_bucket=True)
        self.assertEqual([bucket], await self.storage.list_buckets())

        actual = set(await self.storage.list_files(bucket))
        want = {f"some/of/the/{img}" for img in images}
        self.assertEqual(want, actual)
        for name, content in images.items():
            self.assertEqual(content, await self.storage.file_get(bucket, f"some/of/the/{name}"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.path, ignore_errors=True)
        self.patch.stop()
