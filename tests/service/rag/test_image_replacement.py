import base64
import re
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from langchain_core.language_models import BaseChatModel
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, AIMessage

from deepinsight.service.rag.loaders.base import ParseResult
from deepinsight.service.rag.backends.lightrag_backend import _replace_image_link


_TEST_IMAGES = {
    **{f"im{i}.jpg": f"im{i}".encode() for i in range(42, 45)},
    "im5.png": b"png5"
}
_ENCODED_IMAGES = {
    f"data:image/{'png' if 'png' in k else 'jpg'};base64,{base64.b64encode(v).decode('utf8')}": k
    for k, v in _TEST_IMAGES.items()
}
CHUNK1 = r"""Fig. 7 functions fitted to performance data in all conditions.  
![](images/im42.jpg)
POE (Standard Strength)  
![](images/im5.png)
Fig. 8: (a) The figure on the left shows the point
![alt1](images/im43.jpg)
"""
CHUNK2 = r"""Fig. 9 Logo of Our School.
![alt 2](https://example.com/images/im44.jpg)
Fig. 10 Some redundant picture
![](images/im5.png)
Fig. 11 A failed case
![alt for failed](images/im44.jpg)
"""

CHUNK1_WITH_2PARENT_DIR = r"""Fig. 7 functions fitted to performance data in all conditions.  
![](../../images/im42.jpg)
POE (Standard Strength)  
![](../../images/im5.png)
Fig. 8: (a) The figure on the left shows the point
![alt1](../../images/im43.jpg)
"""
CHUNK2_WITH_2PARENT_DIR = r"""Fig. 9 Logo of Our School.
![alt 2](https://example.com/images/im44.jpg)
Fig. 10 Some redundant picture
![](../../images/im5.png)
Fig. 11 A failed case
![alt for failed](../../images/im44.jpg)
"""

_DESC_REPLACE_MAP = {
    **{f"im{i}.jpg": f"description for {i}th image" for i in range(42, 44)},
    "im5.png": "mock"
}

CHUNK1_REPLACE_BY_VLM = f"""Fig. 7 functions fitted to performance data in all conditions.  
![{_DESC_REPLACE_MAP["im42.jpg"]}](images/im42.jpg)\nPOE (Standard Strength)  
![{_DESC_REPLACE_MAP["im5.png"]}](images/im5.png)
Fig. 8: (a) The figure on the left shows the point
![{_DESC_REPLACE_MAP["im43.jpg"]}](images/im43.jpg)
"""
CHUNK2_REPLACE_BY_VLM = f"""Fig. 9 Logo of Our School.\n![alt 2](https://example.com/images/im44.jpg)
Fig. 10 Some redundant picture
![{_DESC_REPLACE_MAP["im5.png"]}](images/im5.png)
Fig. 11 A failed case\n![alt for failed](images/im44.jpg)
"""

CHUNK1_REPLACE_BY_VLM_WITH_PREFIX = f"""Fig. 7 functions fitted to performance data in all conditions.  
![{_DESC_REPLACE_MAP["im42.jpg"]}](custom/images/im42.jpg)\nPOE (Standard Strength)  
![{_DESC_REPLACE_MAP["im5.png"]}](custom/images/im5.png)
Fig. 8: (a) The figure on the left shows the point
![{_DESC_REPLACE_MAP["im43.jpg"]}](custom/images/im43.jpg)
"""
CHUNK2_REPLACE_BY_VLM_WITH_PREFIX = f"""Fig. 9 Logo of Our School.\n![alt 2](https://example.com/images/im44.jpg)
Fig. 10 Some redundant picture
![{_DESC_REPLACE_MAP["im5.png"]}](custom/images/im5.png)
Fig. 11 A failed case\n![alt for failed](custom/images/im44.jpg)
"""

_REGEX_WITH_LINEBREAK = re.compile(r"""\n!\[(.*?)\]\((images/[a-zA-Z0-9]+\.(jpg|png))\)\n""")


async def _mocked_descript(images: dict[str, bytes], vl: BaseChatModel = None) -> dict[str, str | None]:
    _ = vl
    return {k: _DESC_REPLACE_MAP.get(k) for k in images}


class TestImageReplacePipeline(IsolatedAsyncioTestCase):
    async def test_no_replacement(self):
        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES)
        out = await _replace_image_link(target_doc, replace_alt_text=False)
        self.assertIs(target_doc, out)
        self.assertEqual(target_doc.text[0].page_content, CHUNK1)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2)

        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES)
        await _replace_image_link(target_doc, replace_alt_text=False, prefix="../../")
        self.assertEqual(target_doc.text[0].page_content, CHUNK1_WITH_2PARENT_DIR)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2_WITH_2PARENT_DIR)

        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES,
                                 image_regex=_REGEX_WITH_LINEBREAK)
        await _replace_image_link(target_doc, replace_alt_text=False, prefix="../../")
        self.assertEqual(target_doc.text[0].page_content, CHUNK1_WITH_2PARENT_DIR)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2_WITH_2PARENT_DIR)

    @patch("deepinsight.service.rag.backends.lightrag_backend._create_image_description_batch")
    async def test_llm_replacement(self, mock_desc: MagicMock):
        """If failed, check replace logic outside `_create_image_description_batch`."""
        mock_desc.side_effect = _mocked_descript
        await self._run_with_vlm()

    @patch("deepinsight.service.rag.backends.lightrag_backend._create_image_desc_default_model")
    async def test_with_vlm(self, create_vl: MagicMock):
        """If failed and `test_llm_replacement` succeeded, check `_create_image_description_batch` itself."""
        async def _mocked_llm_invoke(msgs: list[BaseMessage], *args, **kwargs) -> AIMessage:
            self.assertEqual(len(msgs), 1)
            msg = msgs[0]
            self.assertIsInstance(msg.content, list)
            self.assertEqual(len(msg.content), 2)
            self.assertIsInstance(msg.content[1], dict)
            self.assertEqual(msg.content[1]["type"], "text")
            self.assertIsInstance(msg.content[1]["text"], str)

            img_msg = msg.content[0]
            self.assertEqual(img_msg.get("type"), "image_url")
            self.assertIsInstance(img_msg.get("image_url"), dict)

            filename = _ENCODED_IMAGES.get(img_msg["image_url"]["url"])
            self.assertIsNotNone(filename)

            desc = _DESC_REPLACE_MAP.get(filename)
            if not desc:
                raise RuntimeError("Known exception for unittest")
            if "42" in filename:
                return AIMessage(content=[dict(type="text", text=desc)])  # content blocks
            elif "43" in filename:
                return AIMessage(content=[desc])  # list[str]
            return AIMessage(content=desc)

        mocked_vl = MagicMock()
        mocked_vl.ainvoke.side_effect = _mocked_llm_invoke
        create_vl.return_value = mocked_vl
        await self._run_with_vlm()

    async def _run_with_vlm(self):
        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES)
        await _replace_image_link(target_doc)
        self.assertEqual(target_doc.text[0].page_content, CHUNK1_REPLACE_BY_VLM)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2_REPLACE_BY_VLM)

        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES)
        await _replace_image_link(target_doc, prefix="custom/")
        self.assertEqual(target_doc.text[0].page_content, CHUNK1_REPLACE_BY_VLM_WITH_PREFIX)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2_REPLACE_BY_VLM_WITH_PREFIX)

        target_doc = ParseResult(text=[Document(CHUNK1), Document(CHUNK2)], images=_TEST_IMAGES,
                                 image_regex=_REGEX_WITH_LINEBREAK)
        await _replace_image_link(target_doc, prefix="custom/")
        self.assertEqual(target_doc.text[0].page_content, CHUNK1_REPLACE_BY_VLM_WITH_PREFIX)
        self.assertEqual(target_doc.text[1].page_content, CHUNK2_REPLACE_BY_VLM_WITH_PREFIX)
