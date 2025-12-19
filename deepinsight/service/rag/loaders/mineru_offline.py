"""A MinerU client for local deployed service."""
__all__ = ["MinerUOfflineClient", "RunMode", "MineruUnexpectedReturn", "MineruBadRequest"]

import base64
import json
import logging
import os
import re
from enum import Enum
from typing import Annotated, Any

from aiohttp import ClientSession, ClientTimeout, ClientResponse, FormData
from langchain_core.documents import Document
from pydantic import AfterValidator, AnyHttpUrl, Field, ValidationError

from deepinsight.service.rag.loaders.base import BaseLoader, ParseResult
from deepinsight.utils.zip_utils import unzip


TIMEOUT_ENV_ERR = "Environment variable 'MINERU_OFFLINE_MAX_TIMEOUT' can only be a float greater than zero, but got {}."
MINERU_NO_RETURN = "MinerU offline parse service unexpectedly returns nothing for this file."


class MineruUnexpectedReturn(RuntimeError):
    """MinerU returns an unexpected response format."""
    def __init__(self):
        super().__init__("Load parse result from MinerU offline service failed, possibly due to an unsupported"
                         " MinerU server version.")


class MineruBadRequest(RuntimeError):
    """MinerU reject with a bad request error."""
    def __init__(self):
        super().__init__("Failed to make a request to MinerU offline service failed, possibly due to an unsupported"
                         " MinerU server version.")


class MineruInternalError(RuntimeError):
    """Succeeded to load response from MinerU, but MinerU returns an error."""
    input_doc_names: list[str]

    def __init__(self, input_doc_names: list[str]):
        super().__init__("Parse document by MinerU offline service failed.")
        self.input_doc_names = input_doc_names


class RunMode(str, Enum):
    """指示MinerU服务如何返回解析结果。使用`ZIP`模式可能获得更好的编码兼容性。"""
    ZIP = "zip"
    JSON = "json"

    @property
    def is_web_api(self) -> bool:
        return (self == RunMode.JSON) or (self == RunMode.ZIP)


def _base_url_from_env() -> str:
    key = os.environ.get("MINERU_OFFLINE_BASE_URL")
    if not key:
        raise ValueError("Base URL is required to use MinerU offline service. Pass base_url to client or setting "
                         "'MINERU_OFFLINE_BASE_URL' environment variable.")
    try:
        AnyHttpUrl(key)
    except ValidationError:
        raise ValueError("Environment variable 'MINERU_OFFLINE_BASE_URL' is not a valid URL") from None
    return key


def _run_mode_from_env() -> RunMode:
    key = os.environ.get("MINERU_OFFLINE_MODE")
    if not key:
        return RunMode.JSON
    try:
        return RunMode(key.lower())
    except ValueError:
        accept = "', '".join(e.value for e in RunMode)
        raise ValueError(f"Environment variable 'MINERU_OFFLINE_MODE' can only in '{accept}'. Got {key!r}") from None


def _max_timeout_from_env() -> float:
    timeout_str = os.environ.get("MINERU_OFFLINE_MAX_TIMEOUT")
    if not timeout_str:
        return 3600.
    try:
        timeout = float(timeout_str)
    except ValueError:
        raise ValueError(TIMEOUT_ENV_ERR.format(repr(timeout_str))) from None
    if timeout <= 0:
        raise ValueError(TIMEOUT_ENV_ERR.format(repr(timeout_str)))
    return timeout

class MinerUOfflineClient(BaseLoader):
    base_url: Annotated[
        str,
        AfterValidator(lambda v: [AnyHttpUrl(v), v][1]),
        Field(default_factory=_base_url_from_env)
    ]
    max_process_time: Annotated[float, Field(default_factory=_max_timeout_from_env, gt=0)]
    run_mode: Annotated[RunMode, Field(default_factory=_run_mode_from_env)]
    # noinspection RegExpEmptyGroup
    image_regex: re.Pattern[str] = re.compile(r"""!\[()]\((images/[a-f0-9]{64}\.(jpg|png))\)""")

    @staticmethod
    def is_environ_ready() -> bool:
        try:
            _base_url_from_env()
            return True
        except ValueError:
            return False

    @staticmethod
    def _api_load_response_get_errmsg(response_json: Any) -> str:
        content = response_json.get("content", {})
        if isinstance(content, dict):
            error = content.get("error")
            if isinstance(error, str):
                return error
        return "an unknown error"

    @classmethod
    async def _api_load_response_json_validator(cls, map_to_real_name: dict[str, str],
                                                response: ClientResponse) -> dict[str, Any]:
        try:
            json_obj = await response.json(encoding="utf8")
        except Exception as e:
            logging.error(f"Expect MinerU returns a JSON dict when status={response.status} in json response mode,"
                          f" got exception {type(e).__name__}: e", exc_info=True)
            raise MineruUnexpectedReturn() from e
        if not isinstance(json_obj, dict):
            logging.error(f"Expect MinerU returns a JSON dict when status={response.status} in json response mode,"
                          f" got: {json_obj!r}")
            raise MineruUnexpectedReturn()
        if response.status != 200:
            err_msg = cls._api_load_response_get_errmsg(json_obj)
            files = list(map_to_real_name.values())
            logging.error(f"Parse these document by MinerU offline service failed with {err_msg}: {files}")
            raise MineruInternalError(files)
        results = json_obj.get("results") or {}
        if not isinstance(results, dict):
            logging.error(f"Expect MinerU returns a JSON dict of key 'results' when status OK in json response "
                          f"mode, got: {results!r}")
            raise MineruUnexpectedReturn()
        return results

    @classmethod
    async def _api_load_response_zip_validator(cls, map_to_real_name: dict[str, str],
                                               response: ClientResponse) -> dict[str, Any]:
        raw_content = await response.read()
        if response.status != 200:
            try:
                err_status = json.loads(raw_content.decode("utf8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logging.error(f"Expect MinerU returns a JSON dict when status={response.status} in zip response"
                              f" mode, got: {raw_content}", exc_info=True)
                raise MineruUnexpectedReturn() from None
            err_msg = cls._api_load_response_get_errmsg(err_status)
            files = list(map_to_real_name.values())
            logging.error(f"Parse these document by MinerU offline service failed with {err_msg}: {files}")
            raise MineruInternalError(files)
        try:
            return unzip(raw_content)
        except Exception as e:
            logging.error(f"Expect MinerU returns a zip file when status=OK in zip response"
                          f" mode, got exception {type(e).__name__}: {e}", exc_info=True)
            raise MineruUnexpectedReturn() from None

    def model_post_init(self, context: Any, /) -> None:
        self.base_url = self.base_url.rstrip("/")

    async def process(self, name: str, content: bytes) -> ParseResult:
        returns = await self.batch_process({name: content})
        result = list(returns.values())[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def batch_process(self, files: dict[str, bytes]) -> dict[str, ParseResult | Exception]:
        if not files:
            return {}
        if self.run_mode.is_web_api:
            return await self._process_by_web_api(files)
        raise NotImplementedError(f"Run mode {self.run_mode.value!r} for MinerU offline service is not supported yet.")

    def _create_session(self) -> ClientSession:
        conn_time = 10
        return ClientSession(base_url=self.base_url,
                             timeout=ClientTimeout(connect=conn_time, sock_read=self.max_process_time + conn_time),
                             trust_env=True)

    async def _process_by_web_api(self, files: dict[str, bytes]) -> dict[str, ParseResult | Exception]:
        file_list = [
            (f"{index}.{real_name.rsplit('.', 1)[-1]}", real_name, binary)
            for index, (real_name, binary) in enumerate(files.items(), 1)
        ]
        map_to_real_name = {str(index): real_name for index, (_, real_name, _) in enumerate(file_list, 1)}
        # MinerU returns without extension name
        args = dict(
            parse_method="auto",
            return_md=True,
            return_images=True,
            response_format_zip=self.run_mode == RunMode.ZIP
        )
        url = self.base_url + "/file_parse"

        body = FormData()
        for k, v in args.items():
            body.add_field(k, json.dumps(v))
        for fake_name, _, binary in file_list:
            body.add_field("files", binary, filename=fake_name)
        async with self._create_session() as session:
            async with session.post(url, data=body) as response:
                if response.status in (422, 415):
                    try:
                        logging.error(f"MinerU reported a bad request error ({response.status}), may due to an "
                                      f"unsupported version: {await response.read()}")
                    except Exception as e:
                        logging.error(f"MinerU reported a bad request error ({response.status}), may due to an "
                                      f"unsupported version and failed to load response with {type(e).__name__}: {e}")
                    raise MineruBadRequest()
                if self.run_mode == RunMode.JSON:
                    return await self._api_load_response_json(map_to_real_name, response)
                return await self._api_load_response_zip(map_to_real_name, response)

    async def _api_load_response_json(self, map_to_real_name: dict[str, str],
                                      response: ClientResponse) -> dict[str, ParseResult | Exception]:
        """Used when `self.run_mode` is `RunMode.JSON`."""
        json_obj = await self._api_load_response_json_validator(map_to_real_name, response)
        output_dict: dict[str, ParseResult | Exception] = {}

        for fake, real_name in map_to_real_name.items():
            result = json_obj.get(fake)
            if not (result and isinstance(result, dict)):
                logging.warning(f"MinerU unexpectedly returns nothing for file {real_name!r}")
                output_dict[real_name] = RuntimeError(MINERU_NO_RETURN)
                continue
            markdown = result.get("md_content")
            if not isinstance(markdown, str):
                logging.warning(f"MinerU unexpectedly returns a {type(markdown).__name__} for content of file "
                                f"{real_name!r} (which expected to be str), got: {markdown}")
                output_dict[real_name] = MineruUnexpectedReturn()
                continue
            images = result.get("images", {})
            if not isinstance(images, dict):
                logging.warning(f"MinerU unexpectedly returns a {type(images).__name__} for images of file "
                                f"{real_name!r} (which expected to be dict), got: {images}")
                output_dict[real_name] = MineruUnexpectedReturn()
                continue
            decoded_images: dict[str, bytes] = {}
            broken_images: dict[str, Any] = {}
            for img_path, raw_content in images.items():
                if not isinstance(raw_content, str):
                    broken_images[img_path] = raw_content
                try:
                    decoded_images[img_path] = base64.b64decode(raw_content.split(",", 1)[-1])
                except Exception as e:
                    logging.error(f"MinerU client decode image {img_path!r} of file {real_name!r} failed with "
                                  f"{type(e).__name__}: {e}", exc_info=True)
                    broken_images[img_path] = raw_content
            output_dict[real_name] = ParseResult(text=[Document(markdown)], images=decoded_images,
                                                 image_regex=self.image_regex)
        return output_dict

    async def _api_load_response_zip(self, map_to_real_name: dict[str, str],
                                     response: ClientResponse) -> dict[str, ParseResult | Exception]:
        """Used when `self.run_mode` is `RunMode.ZIP`."""
        unzipped = await self._api_load_response_zip_validator(map_to_real_name, response)
        output_dict: dict[str, ParseResult | Exception] = {}

        for fake, real_name in map_to_real_name.items():
            result = unzipped.get(fake)
            if result is None:
                logging.warning(f"MinerU unexpectedly returns nothing for file {real_name!r}")
                output_dict[real_name] = RuntimeError(MINERU_NO_RETURN)
            elif not isinstance(result, dict):
                logging.warning(f"MinerU unexpectedly returns a {type(result).__name__} for file "
                                f"{real_name!r} (which expected to be dict)")
                output_dict[real_name] = MineruUnexpectedReturn()
                continue
            markdown_bin = result.get(f"{fake}.md")
            if not isinstance(markdown_bin, bytes):
                logging.warning(f"MinerU unexpectedly returns a {type(markdown_bin).__name__} for content of file "
                                f"{real_name!r} (which expected to be bytes), got: {markdown_bin}")
                output_dict[real_name] = MineruUnexpectedReturn()
                continue
            try:
                markdown = markdown_bin.decode("utf8")
            except UnicodeDecodeError as e:
                output_dict[real_name] = e
                continue
            images = result.get("images", {})
            if not (isinstance(images, dict) and all(isinstance(img, bytes) for img in images.values())):
                logging.warning(f"MinerU unexpectedly returns a {type(images).__name__} for images of file "
                                f"{real_name!r} (which expected to be dict[str, bytes]), got: {images}")
                output_dict[real_name] = MineruUnexpectedReturn()
                continue
            output_dict[real_name] = ParseResult(text=[Document(markdown)], images=images, image_regex=self.image_regex)
        return output_dict
