import asyncio
import logging
import os
import re
from typing import Annotated

from aiohttp import ClientSession, ClientTimeout, client_exceptions, ClientResponse
from langchain_text_splitters import MarkdownHeaderTextSplitter
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr

from deepinsight.service.rag.loaders.base import BaseLoader, ParseResult
from deepinsight.utils.zip_utils import unzip


def _api_key_from_env() -> SecretStr:
    key = os.environ.get("MINERU_ONLINE_API_KEY")
    if not key:
        raise ValueError("API KEY is required to use MinerU online service. Pass api_key to client or setting "
                         "'MINERU_ONLINE_API_KEY' environment variable.")
    return SecretStr(key)


class MinerUOnlineClient(BaseLoader):
    api_key: Annotated[SecretStr, Field(default_factory=_api_key_from_env)]
    base_url: str = "https://mineru.net/api/v4/"
    state_query_interval: float = 15
    state_query_timeout: float = 60 * 60
    image_regex: re.Pattern[str] = re.compile(r"""!\[()\]\((images/[a-f0-9]{64}\.(jpg|png))\)""")

    async def process(self, name: str, content: bytes) -> ParseResult:
        returns = await self.batch_process({name: content})
        result = list(returns.values())[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def batch_process(self, files: dict[str, bytes]) -> dict[str, ParseResult | Exception]:
        async with self._create_session() as session:
            batch_id = await self._submit_tasks(session, files)
            task_result = await self._wait_batch_task_end(session, batch_id)
            log_str = "Batch parse request to MinerU online service ended.\n"
            log_str += "\n".join(
                f"  - {task.file_name!r} {task.state_description}"
                for task in task_result.extract_result
            )
            logging.info(log_str)
            succeeded_tasks = {
                task.file_name: task.full_zip_url
                for task in task_result.extract_result
                if task.succeeded
            }
            failed_tasks = {
                task.file_name: RuntimeError(f"Parse failed {task.state_description}")
                for task in task_result.extract_result
                if not task.succeeded
            }
            downloaded = await self._batch_download_results(session, succeeded_tasks)
            downloaded.update(failed_tasks)
            if set(downloaded) != set(files):
                logging.error(f"Unexpected processed files not meets parse request. Want {set(files)}, "
                              f"got {set(downloaded)}.")
                raise AssertionError("Unexpected processed files not meets parse request.")
            return downloaded

    async def _wait_batch_task_end(self, session: ClientSession, task: "_SubmittedTask") -> "_BatchStatusResult.Data":
        task_status = None
        try:
            async with asyncio.timeout(self.state_query_timeout):
                while True:
                    task_status = await self._get_batch_status(session, task.batch_id)
                    unfinish_num = task_status.unfinish_task_num(task.upload_failures)
                    num_tasks = len(task_status.extract_result)
                    if not unfinish_num:
                        return task_status
                    logging.debug("MinerU got %d of %d task ends for batch task %r. Fetch for next try after "
                                  "%s seconds",
                                  num_tasks - unfinish_num, num_tasks, task.batch_id, self.state_query_interval)
                    await asyncio.sleep(self.state_query_interval)
        except asyncio.TimeoutError:
            if task_status is None:
                raise RuntimeError(f"Waiting for MinerU online service parsing document timeout after "
                                   f"{self.state_query_timeout} seconds.")
            logging.warning(f"Waiting for MinerU online service parsing task {task.batch_id=!r} timeout after "
                            f"{self.state_query_timeout} seconds. Regards unfinished tasks as failure.")
            return task_status

    async def _submit_tasks(self, session: ClientSession, files: dict[str, bytes]) -> "_SubmittedTask":
        """Return the batch ID of the given file tasks. `files` should not be empty."""
        file_list = list(files.items())
        create_task_request = dict(
            files = [
                dict(name=name, data_id=str(i), is_ocr=True)
                for i, (name, _) in enumerate(file_list)
            ],
            model_version="vlm"
        )
        create_resp = await self._request(session, "POST", "./file-urls/batch", json_=create_task_request)
        created_task = _CreateBatchTaskResult.model_validate(await create_resp.json())
        created_task.check_ok()
        batch_id = created_task.data.batch_id

        upload_tasks = [
            self._upload_with_retry(session, name, batch_id, url, content, retry_count=5)
            for url, (name, content) in zip(created_task.data.file_urls, file_list)
        ]
        logging.debug("Begin submitting %d MinerU uploading tasks for batch_id=%r",
                      len(upload_tasks), batch_id)
        upload_failures = [
            (name, exception)
            for (name, _), exception
            in zip(file_list, await asyncio.gather(*upload_tasks, return_exceptions=True))
            if exception
        ]
        if upload_failures:
            logging.warning(f"Upload task for {batch_id=!r} got {len(upload_failures)} failures:\n" +
                            f"\n".join(f"{name!r} failed with {type(e).__name__}: {e}" for name, e in upload_failures))
        logging.debug("Upload %d files to MinerU online service ended for batch_id=%r",
                      len(upload_tasks), batch_id)
        if len(upload_tasks) == len(upload_failures):
            raise RuntimeError(f"All upload tasks for {batch_id=!r} failed! Abort.")
        return _SubmittedTask(batch_id=batch_id, upload_failures=set(name for name, _ in upload_failures))

    async def _upload_with_retry(self, session: ClientSession,
                                 filename: str, batch_id: str,
                                 url: str, file: bytes, retry_count: int) -> None:
        last = RuntimeError("Unexpected error. You should not see this.")
        for i in range(retry_count):
            try:
                response = await self._request(session, "PUT", url, body=file)
                response.raise_for_status()
                return
            except Exception as e:
                logging.warning(f"Uploading file {filename!r} (of batch {batch_id} ) to {url} failed for the {i} "
                                f"count (max {retry_count} with {type(e).__name__}: {e}", exc_info=True)
                last = e
        raise last

    async def _get_batch_status(self, session: ClientSession, batch_id: str) -> "_BatchStatusResult.Data":
        response = await self._request(session, "GET", f"./extract-results/batch/{batch_id}")
        from pydantic import ValidationError
        try:
            obj = _BatchStatusResult.model_validate(await response.json())
        except ValidationError:
            print(await response.json())
            raise
        if obj.code != 0:
            raise RuntimeError("Query for task status from MinerU online service with an unexpected error.")
        return obj.data

    async def _batch_download_results(self, session: ClientSession,
                                      result_url: dict[str, str]) -> dict[str, ParseResult | Exception]:
        tasks = [self._download_result(session, filename, url) for filename, url in result_url.items()]
        completed = dict(await asyncio.gather(*tasks, return_exceptions=True))
        return completed

    async def _download_result(self, session: ClientSession, file_name: str, url: str) -> tuple[str, ParseResult]:
        zip_file = await (await self._request(session, "GET", url)).read()
        unzipped = _FileResult.model_validate(unzip(zip_file), by_alias=True)
        split = (
            MarkdownHeaderTextSplitter(headers_to_split_on=[("#" * i, f"H{i}") for i in range(1, 4)],
                                       strip_headers=False)
            .split_text(unzipped.markdown.decode("utf8"))
        )
        return file_name, ParseResult(text=split, images=unzipped.images, image_regex=self.image_regex)

    def _create_session(self) -> ClientSession:
        return ClientSession(base_url=self.base_url, timeout=ClientTimeout(connect=10, sock_read=20), trust_env=True)

    async def _request(self, session: ClientSession, method: str, path: str, body=None, json_=None) -> ClientResponse:
        try:
            headers = {"Authorization": f"Bearer {self.api_key.get_secret_value()}"}
            if json_:
                args = dict(json=json_)
            else:
                args = dict(data=body)
                headers["Content-Type"] = ""  # otherwise, upload file will get a 403 response.
            response = await session.request(method, url=path,
                                             headers=headers,
                                             **args)
            if response.status == 429:  # HTTP too many requests
                raise RuntimeError("Too many request to MinerU")
            response.raise_for_status()
            return response
        except client_exceptions.ClientError as e:
            logging.error(f"{method} to {self.base_url} failed with {type(e).__name__}: {e}", exc_info=True)
            raise


class _CreateBatchTaskResult(BaseModel):
    class Data(BaseModel):
        batch_id: str
        file_urls: list[Annotated[str, AnyHttpUrl]]
    
    code: int
    data: Data
    msg: str | None = None
    
    def check_ok(self):
        if self.code != 0:
            raise RuntimeError(f"Create MinerU parsing task failed. Server returns: {self.msg}")


class _BatchStatusResult(BaseModel):
    class Data(BaseModel):
        class Status(BaseModel):
            data_id: str
            file_name: str
            state: str
            full_zip_url: str = None
            err_msg: str = None

            @property
            def succeeded(self):
                return self.state == "done"

            @property
            def is_failed(self):
                return self.state == "failed"

            @property
            def is_running(self):
                return self.state not in ("done", "failed")

            @property
            def is_waiting_file(self):
                return self.state == "waiting-file"

            @property
            def state_description(self):
                if self.succeeded:
                    return "OK"
                if self.is_failed:
                    return f"❌: {self.err_msg}"
                if self.is_running:
                    return f"⏰: Waiting for task done timeout."
                return "❌: Upload failed."

        extract_result: list[Status]

        def unfinish_task_num(self, upload_failures: set[str]) -> int:
            return sum(
                1
                for task in self.extract_result
                if task.is_running or (task.is_waiting_file and task.file_name not in upload_failures)
            )

    code: int
    data: Data


class _FileResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    markdown: Annotated[bytes, Field(alias="full.md")]
    images: dict[str, bytes]


class _SubmittedTask(BaseModel):
    batch_id: str
    upload_failures: set[str]
