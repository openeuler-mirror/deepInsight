"""A Tavily tool creator which manages several API KEYs, or using user's API key."""
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import logging
import asyncio
import threading
from typing import Any, NamedTuple, Optional, Literal

from aiohttp import ClientSession, ClientTimeout, ClientError
from langchain_tavily import TavilySearch as _OriginTavily
from mcp.server.fastmcp.exceptions import ValidationError
from pydantic import BaseModel, PrivateAttr, SecretStr
from tavily import TavilyClient, AsyncTavilyClient
from tavily.errors import (
    InvalidAPIKeyError as TavilyInvalidKeyError,
    ForbiddenError as TavilyOutOfPlanLimitError  # UsageLimitExceededError is for rate limit, not out of balance
)

from deepinsight.utils.singleton import make_singleton

TAVILY_BASE_URL = "https://api.tavily.com"


class TavilyError(RuntimeError):
    ...


class TavilyRetryLimitError(TavilyError):
    def __init__(self, times: int):
        super().__init__(f"Try to invoke Tavily with too many unusable API key for {times} times.")


class TavilyNoAvailableKeyError(TavilyError):
    def __init__(self, registered_count: int):
        super().__init__(f"No Tavily API key available. All {registered_count} key(s) are invalid or out of limit.")


class TavilyNoKeyError(TavilyError):
    def __init__(self):
        super().__init__("No available API key for Tavily Search.")


class TavilyNoEnvError(TavilyError):
    def __init__(self):
        super().__init__("No available tavily keys. Set single key as environ variable 'TAVILY_API_KEY' or "
                         "a group of key separated by comma ',' as environ variable 'TAVILY_API_KEYS' to "
                         "use Tavily Search.")


class _NotThisManagerError(RuntimeError):
    def __init__(self):
        super().__init__("Someone try to refresh a Tavily client which not belongs to this manager. "
                         "If you see this message, make an issue to DeepInsight group.")


class KeyUsage(NamedTuple):
    valid: bool
    usage: int | None  # Be `None` only when `valid` is `False`
    limit: int | None  # Be `None` only when `valid` is `False`


class UsageResponse(BaseModel):
    class Account(BaseModel):
        plan_usage: int
        plan_limit: int
        paygo_usage: int  # noqa: `paygo` in response body
        paygo_limit: int | None  # noqa: `paygo` in response body

    class KeyUsage(BaseModel):
        usage: int
        limit: int | None

    key: KeyUsage
    account: Account

    @property
    def limit(self) -> int:
        if self.key.limit is not None:
            return self.key.limit
        if self.account.paygo_limit is not None:
            return self.account.paygo_limit
        return self.account.plan_limit


class _TavilyClientGroup:
    key: SecretStr
    async_client: AsyncTavilyClient
    client: TavilyClient
    predict: int

    def __init__(self, key: str, base_url: str = TAVILY_BASE_URL, proxy: dict = None, predict: int = 0):
        self.key = SecretStr(key)
        proxy = dict(proxy or {})
        if not proxy.get("https"):
            proxy["https"] = os.getenv("TAVILY_HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")
        if not proxy.get("http"):
            proxy["http"] = os.getenv("TAVILY_HTTP_PROXY") or os.getenv("http_proxy") or os.getenv("HTTP_PROXY")

        self.async_client = AsyncTavilyClient(api_key=key, proxies=proxy, api_base_url=base_url)
        self.client = TavilyClient(api_key=key, proxies=proxy, api_base_url=base_url)
        self.predict = predict


class TavilyBaseKeyManager(ABC):
    @abstractmethod
    def get_client(self, last: _TavilyClientGroup = None, last_invalid: bool = False) -> _TavilyClientGroup:
        ...

    def tool(self, **kwargs) -> "TavilySearch":
        return TavilySearch(self, **kwargs)


class SingleKeyManager(TavilyBaseKeyManager):
    """No rotate nor fetch for usage. If `get_client` reports an invalid / out-of-limit key, raise an Exception."""

    def __init__(self, key: str, base_url: str = TAVILY_BASE_URL, proxy: dict = None):
        if not key:
            raise TavilyNoKeyError()
        self.__client = _TavilyClientGroup(key, base_url, proxy)

    def get_client(self, last: _TavilyClientGroup = None, last_invalid: bool = False) -> _TavilyClientGroup:
        if not last:
            return self.__client
        if last is not self.__client:
            raise _NotThisManagerError()
        raise TavilyNoAvailableKeyError(1)


class RotatingKeyManager(TavilyBaseKeyManager):
    @dataclass
    class _Summary:
        out_of_limit_count: int
        valid_key_count: int
        predict_balance: int

    # Run an independent event loop in a single thread to avoid from async deadlock.
    __thread: threading.Thread
    __loop: asyncio.AbstractEventLoop
    __refresh_lock: asyncio.Lock

    # Statistic args
    __all_keys: tuple[str, ...]
    # States
    __valid_keys: set[str]
    """Keys if is valid (but still may out-of-limit)"""
    __usable_keys: dict[str, _TavilyClientGroup]
    """Keys if is valid and not out-of-limit at last refresh time."""
    __last_refreshed_status: _Summary | None

    def __init__(self, keys: list[str], base_url: str = TAVILY_BASE_URL, proxy: dict = None):
        # Tavily required args
        self.tavily_base_url = base_url or TAVILY_BASE_URL
        self.proxy = proxy

        self.usage_base_url = base_url.rstrip("/") + "/"  # for aiohttp

        # environ args and make an independent event loop
        self.__loop = None  # type: ignore # inited later
        self.__refresh_lock = None  # type: ignore # inited later
        self.__thread = threading.Thread(target=self.__daemon_main, name=f"TavilyDaemon_0x{id(self):X}", daemon=True)
        self.__thread.start()

        # state and statistic fields
        self.__valid_keys = set(keys)
        self.__all_keys = tuple(self.__valid_keys)
        self.__usable_keys = {}
        self.__last_refreshed_status = None

        # wait daemon ready
        while not self.__loop:
            time.sleep(0.5)

    def __del__(self):
        if not hasattr(self, "__loop"):
            return
        if self.__loop and self.__loop.is_running():
            self.__loop.call_soon_threadsafe(asyncio.create_task, self.__daemon_exit())

    @staticmethod
    async def __fetch_one(session: ClientSession, key: str) -> KeyUsage | None:
        """Only returns None when network error."""
        try:
            async with session.get("/usage", headers=dict(Authorization=f"Bear {key}")) as response:
                response_json = await response.json(content_type=None)  # Tavily does not return a legal MIME type
        except ClientError as e:
            logging.error(f"Failed to fetch one Tavily key usage with {type(e).__name__}: {e}", exc_info=True)
            return None
        if response.status != 200:
            return KeyUsage(valid=False, usage=None, limit=None)
        try:
            usage = UsageResponse.model_validate(response_json)
        except ValidationError as e:
            logging.error(f"Failed to fetch one Tavily key usage with a ValidationError, perhaps Tavily "
                          f"API definition changed: {e}", exc_info=True)
            return None
        used_count = min(usage.key.usage, usage.limit)  # usage may >= limit, reset to equal
        return KeyUsage(valid=True, usage=used_count, limit=usage.limit)

    def get_client(self, last: _TavilyClientGroup = None, last_invalid: bool = False) -> _TavilyClientGroup:
        return asyncio.run_coroutine_threadsafe(
            self.__get_client(last, last_invalid), loop=self.__loop
        ).result()

    def _create_session(self) -> ClientSession:
        return ClientSession(base_url=self.usage_base_url, timeout=ClientTimeout(connect=20, sock_read=10),
                             trust_env=True)

    def __apply_balance_result(self, last_valid_keys: list[str], response: list[KeyUsage | None]):
        """This should be call within self.__refresh_lock"""
        network_err_keys: list[str] = []
        out_of_use_keys: list[str] = []
        next_valid_keys: list[tuple[str, int]] = []
        expired: list[str] = []
        for key, usage in zip(last_valid_keys, response):
            if usage is None:
                network_err_keys.append(key)
                next_valid_keys.append((key, 0))
                continue
            usage: KeyUsage
            if not usage.valid:
                expired.append(key)
                continue
            left = usage.limit - usage.usage
            if left:
                next_valid_keys.append((key, left))
            else:
                out_of_use_keys.append(key)
        next_usable_keys = [(k, count) for k, count in next_valid_keys if count]
        total = sum(i for _, i in next_usable_keys)
        summary = self._Summary(out_of_limit_count=len(out_of_use_keys),
                                valid_key_count=len(last_valid_keys) - len(expired),
                                predict_balance=total)

        # update status. Network error regards as possible
        self.__valid_keys = set(k for k, _ in next_valid_keys).union(network_err_keys)
        self.__apply_usable_state(next_usable_keys + [(k, 0) for k in network_err_keys])
        if network_err_keys:
            logging.warning(f"Updating Tavily API Key balance with {len(network_err_keys)} network errors.")
        has_state_change = summary != self.__last_refreshed_status
        self.__last_refreshed_status = summary

        if has_state_change:
            self.__log_status("refresh task")

    def __apply_usable_state(self, new_usable_keys: list[tuple[str, int]]) -> None:
        """This should be call within self.__refresh_lock"""
        existing_clients = self.__usable_keys
        new_clients = {}
        for key, balance in new_usable_keys:
            client = existing_clients.get(key) or _TavilyClientGroup(key, self.tavily_base_url, self.proxy)
            client.predict = balance
            new_clients[key] = client
        self.__usable_keys = new_clients

    async def __get_client(self, last: _TavilyClientGroup | None, last_invalid: bool) -> _TavilyClientGroup:
        async with self.__refresh_lock:
            self.__set_key_unusable(last, last_invalid)
            if not self.__usable_keys:
                await self.__query_key_usage_and_update()
            return self.__next_usable_key()

    def __daemon_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.__refresh_lock = asyncio.Lock()
        self.__loop = loop
        logging.info(f"Daemon for Tavily key manager at 0x{id(self):X} is running.")
        loop.run_forever()
        logging.info(f"Daemon for Tavily key manager at 0x{id(self):X} exited.")

    async def __daemon_exit(self) -> None:
        self.__loop.close()

    def __log_status(self, reason: str) -> None:
        summary = self.__last_refreshed_status
        expired_count = len(self.__all_keys) - summary.valid_key_count
        logging.info(f"Tavily API key status updated (by {reason}). {len(self.__all_keys)} keys "
                     f"({expired_count} expired, {summary.out_of_limit_count} out of limit), "
                     f"estimated no more than {summary.predict_balance} left.")

    def __next_usable_key(self) -> _TavilyClientGroup:
        if not self.__usable_keys:
            raise TavilyNoAvailableKeyError(len(self.__all_keys))
        first_inserted = next(iter(self.__usable_keys))  # to prevent too many conflict
        client = self.__usable_keys.pop(first_inserted)
        self.__usable_keys[first_inserted] = client
        return client

    async def __query_key_usage_and_update(self) -> None:
        last_valid_keys = list(self.__valid_keys)
        if not last_valid_keys:
            raise TavilyNoAvailableKeyError(len(self.__all_keys))
        async with self._create_session() as session:
            response = await asyncio.gather(*(self.__fetch_one(session, key) for key in last_valid_keys))
        self.__apply_balance_result(last_valid_keys, response)

    def __set_key_unusable(self, current: _TavilyClientGroup | None, invalid: bool) -> None:
        """This should be call within self.__refresh_lock"""
        if not current:
            return
        key = current.key.get_secret_value()
        if invalid and key in self.__valid_keys:
            self.__valid_keys.remove(key)
            self.__last_refreshed_status.valid_key_count -= 1
        if key in self.__usable_keys:
            self.__last_refreshed_status.predict_balance -= self.__usable_keys.pop(key).predict
        if not invalid:
            self.__last_refreshed_status.out_of_limit_count += 1
        self.__log_status("invalid key report" if invalid else "out of limit report")


class EnvironKeyManager(RotatingKeyManager):
    def __init__(self):
        env_key = os.getenv("TAVILY_API_KEYS") or os.getenv("TAVILY_API_KEY")
        if not env_key:
            raise TavilyNoEnvError()
        keys = env_key.split(",")
        super().__init__(keys)


def tavily_key_manager():
    return make_singleton(EnvironKeyManager)


class TavilySearch(_OriginTavily):
    __mgr: TavilyBaseKeyManager = PrivateAttr(None)
    __current_client: _TavilyClientGroup = PrivateAttr()

    def __init__(self, mgr: TavilyBaseKeyManager, **kwargs):
        kwargs["tavily_api_key"] = "*"  # managed. This is a mock
        super().__init__(**kwargs)
        self.__mgr = mgr
        self.__current_client = mgr.get_client()

    max_key_retry_count: int = 10

    timeout: float = 30.0
    """Request timeout in seconds."""

    def extract(self, urls: list[str]) -> dict[str, Any]:
        for _ in range(self.max_key_retry_count):
            client = self.__current_client  # prevent from concurrent competition
            try:
                return client.client.extract(urls=urls)
            except TavilyOutOfPlanLimitError:
                logging.warning("Extract by Tavily failed with an out of usage limit exception. "
                                "Try to get another key and retry.")
                last_invalid = False
            except TavilyInvalidKeyError:
                logging.warning("Extract by Tavily failed with an invalid key. Try to get another key and retry.")
                last_invalid = True
            self.__current_client = self.__mgr.get_client(client, last_invalid=last_invalid)
        raise TavilyRetryLimitError(self.max_key_retry_count)

    async def search_async(self,
                           query: str,
                           include_domains: list[str] | None = None,
                           exclude_domains: list[str] | None = None,
                           search_depth: Literal["basic", "advanced"] | None = None,
                           include_images: bool | None = None,
                           include_image_descriptions: bool | None = None,
                           time_range: Literal["day", "week", "month", "year"] | None = None,
                           topic: Literal["general", "news", "finance"] | None = None,
                           include_favicon: bool | None = None,
                           start_date: str | None = None,
                           end_date: Optional[str] = None) -> dict[str, Any]:
        for _ in range(self.max_key_retry_count):
            client = self.__current_client  # prevent from concurrent competition
            try:
                return await client.async_client.search(
                    query=query,
                    search_depth=search_depth or self.search_depth,
                    topic=topic or self.topic,
                    time_range=time_range or self.time_range,
                    start_date=start_date,
                    end_date=end_date,
                    max_results=self.max_results,
                    include_domains=include_domains or self.include_domains,
                    exclude_domains=exclude_domains or self.exclude_domains,
                    include_answer=self.include_answer,
                    include_raw_content=self.include_raw_content,
                    include_images=include_images or self.include_images,
                    timeout=self.timeout,
                    country=self.country,
                    auto_parameters=self.auto_parameters,
                    include_favicon=include_favicon or self.include_favicon,
                    # kwargs from tool attributes
                    include_image_descriptions=include_image_descriptions or self.include_image_descriptions
                )
            except TavilyOutOfPlanLimitError:
                logging.warning("Search on Tavily failed with an out of usage limit exception. "
                                "Try to get another key and retry.")
                last_invalid = False
            except TavilyInvalidKeyError:
                logging.warning("Search on Tavily failed with an invalid key. Try to get another key and retry.")
                last_invalid = True
            self.__current_client = self.__mgr.get_client(client, last_invalid=last_invalid)
        raise TavilyRetryLimitError(self.max_key_retry_count)

    def _run(self,
             query: str,
             include_domains: list[str] | None = None,
             exclude_domains: list[str] | None = None,
             search_depth: Literal["basic", "advanced"] | None = None,
             include_images: bool | None = None,
             time_range: Literal["day", "week", "month", "year"] | None = None,
             topic: Literal["general", "news", "finance"] | None = None,
             include_favicon: bool | None = None,
             start_date: str | None = None,
             end_date: str | None = None,
             run_manager=None) -> dict[str, Any]:
        for _ in range(self.max_key_retry_count):
            client = self.__current_client  # prevent from concurrent competition
            try:
                return client.client.search(
                    query=query,
                    search_depth=search_depth or self.search_depth,
                    topic=topic or self.topic,
                    time_range=time_range or self.time_range,
                    start_date=start_date,
                    end_date=end_date,
                    max_results=self.max_results,
                    include_domains=include_domains or self.include_domains,
                    exclude_domains=exclude_domains or self.exclude_domains,
                    include_answer=self.include_answer,
                    include_raw_content=self.include_raw_content,
                    include_images=include_images or self.include_images,
                    timeout=self.timeout,
                    country=self.country,
                    auto_parameters=self.auto_parameters,
                    include_favicon=include_favicon or self.include_favicon,
                    # kwargs from tool attributes
                    include_image_descriptions=self.include_image_descriptions
                )
            except TavilyOutOfPlanLimitError:
                logging.warning("Search on Tavily failed with an out of usage limit exception. "
                                "Try to get another key and retry.")
                last_invalid = False
            except TavilyInvalidKeyError:
                logging.warning("Search on Tavily failed with an invalid key. Try to get another key and retry.")
                last_invalid = True
            self.__current_client = self.__mgr.get_client(client, last_invalid=last_invalid)
        raise TavilyRetryLimitError(self.max_key_retry_count)

    async def _arun(self,
                    query: str,
                    include_domains: list[str] | None = None,
                    exclude_domains: list[str] | None = None,
                    search_depth: Literal["basic", "advanced"] | None = None,
                    include_images: bool | None = None,
                    time_range: Literal["day", "week", "month", "year"] | None = None,
                    topic: Literal["general", "news", "finance"] | None = None,
                    include_favicon: bool | None = None,
                    start_date: str | None = None,
                    end_date: str | None = None,
                    run_manager=None) -> dict[str, Any]:
        return await self.search_async(query=query,
                                       include_domains=include_domains,
                                       exclude_domains=exclude_domains,
                                       search_depth=search_depth,
                                       include_images=include_images,
                                       time_range=time_range,
                                       topic=topic,
                                       include_favicon=include_favicon,
                                       start_date=start_date,
                                       end_date=end_date)
