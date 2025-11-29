# This tool includes some data (ISO-3166-1 Alpha2 code of the country in the organization information) sourced
# from GeoNames, available under the Creative Commons Attribution 4.0 License (CC BY 4.0).
"""An organization search tools using ROR database.

Research Organization Registry (ROR) is a global registry of open persistent identifiers for research organizations.
We use its database through ROR API to retrieve parent-child relationships between institutions
in order to consolidate outcomes under different sub-organizations.
"""
import asyncio
import json
import os
from collections import defaultdict
import logging
from typing import Annotated, Any, Iterable, Literal, MutableMapping, Type, TypedDict, TypeVar
from urllib.parse import quote as quote_url

from aiohttp import ClientSession, ClientTimeout
from cachetools import LRUCache
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.func import entrypoint
from langchain.agents import create_agent
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Ignore = Any
_ELASTIC_OPERATORS = set(r'+-=&|><!(){}[]^"~*?:\/')
_Model = TypeVar("_Model", bound=BaseModel)
OrganizationType = Literal["archive", "company", "education", "facility", "funder",
                           "government", "healthcare", "nonprofit", "other"] | str


class GeoNames(BaseModel):
    model_config = ConfigDict(extra="ignore")

    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2,2}$")]


class Location(BaseModel):
    geonames_details: GeoNames
    geonames_id: int


class Name(BaseModel):
    lang: str | None
    types: list[str]
    value: str


class Relationship(BaseModel):
    id: str
    label: str
    type: str


class Organization(BaseModel):
    id: str
    status: Literal["active", "inactive", "withdrawn"]
    types: list[OrganizationType]

    admin: Ignore = None
    domains: list[str]
    established: Ignore = None
    external_ids: Ignore = None
    links: Ignore = None
    locations: list[Location]
    names: list[Name]
    relationships: list[Relationship]

    def __str__(self):
        return repr(self.ror_name) + (f" ({self.id}{'' if self.is_active else ' ( 🗑️ not active)'}"
                                      f" | types={', '.join(self.types) or '?'})")

    @property
    def first_country_code(self) -> Annotated[str, Field(pattern=r"^[A-Z]{2,2}$")] | None:
        """Return the first Alpha2 country code of this organization if it has."""
        if not self.locations:
            return None
        return self.locations[0].geonames_details.country_code

    @property
    def is_active(self) -> bool:
        """Whether this organization info is an active record."""
        return self.status == "active"

    @property
    def ror_name(self) -> str:
        """Get the name that tagged with 'ror_display'."""
        ror_name = [name for name in self.names if "ror_display" in name.types]
        if len(ror_name) != 1:
            raise ValueError(f"Expect one name with 'ror_display' tag, but got{len(ror_name)}.")
        return ror_name[0].value

    @property
    def parent(self) -> Relationship | None:
        """Find the parent and returns `None` if not found."""
        parents = [org for org in self.relationships if org.type == "parent"] or [None]
        return parents[0]

    @property
    def simplified_dump(self) -> dict:
        """Make a simplified model_dump for LLM."""
        ror_name = self.ror_name
        return {
            "ror_id": self.id,
            "ror_name": ror_name,
            "aliases": [name.value for name in self.names if name.value != ror_name],
            "country_code": self.first_country_code,
        }


class Match(BaseModel):
    score: Annotated[float, Field(ge=0)]
    matching_type: Literal["EXACT", "FUZZY", "PARTIAL"] | str
    chosen: bool
    organization: Organization
    substring: Ignore = None

    def __str__(self):
        return f"({self.score:.2f}, {'chosen' if self.chosen else '      '}) {self.organization}"

    @classmethod
    def merge_organization(cls: Type[_Model], parent: Organization, children: "list[Match]") -> _Model:
        """Merge weight of a list of child organizations into their parent."""
        return Match(
            score=sum(child.score for child in children) if children else 0,
            matching_type=min(child.matching_type for child in children) if children else "EXACT",
            chosen=any(child.chosen for child in children) if children else True,
            organization=parent
        )

    def merge(self, other: "Match"):
        """Merge another match record into this record."""
        self.score += other.score
        self.chosen |= other.chosen
        return self


class RORMatchResponse(BaseModel):
    number_of_results: int
    items: list[Match]


_ror_cache: MutableMapping[str, Organization] = LRUCache(maxsize=1024)


def _get_api_base():
    return os.environ.get("ROR_API_BASE") or "https://api.ror.org/"


def _get_verify_env():
    return os.environ.get("ROR_VERIFY_SSL") in ("0", "FALSE", "False", "false")


class RORQueryResponse(BaseModel):
    items: list[Organization]
    meta: Ignore


class RORClient(BaseModel):
    class RateLimit(Exception):
        """Exception when received rate limited error from ROR API."""
        _HAS_KEY_MSG = "ROR rate limit!"
        _NO_KEY_MSG = _HAS_KEY_MSG + " Add an ROR Client Key to lift rate limits."

        def __init__(self, has_key: bool):
            super().__init__(self._HAS_KEY_MSG if has_key else self._NO_KEY_MSG)

    verify_ssl: Annotated[bool, Field(default_factory=_get_verify_env)]
    client_id: str = None
    max_retry_per_request: int = 3
    api_base: Annotated[str, Field(default_factory=_get_api_base)]

    @property
    def _headers(self):
        return {"Client-Id": self.client_id} if self.client_id else {}

    def __str__(self):
        return f"<ROR client to {self.api_base!r} with{'' if self.client_id else 'out'} client ID>"

    @staticmethod
    def _escape(url: str, **kwargs: str) -> str:
        escaped_args = {}
        for key, value in kwargs.items():
            es_escape = "".join(f"\\{s}" if s in _ELASTIC_OPERATORS else s for s in value)
            escaped_args[key] = quote_url(es_escape, safe="")
        return url.format(**escaped_args)

    @staticmethod
    def _format_organizations(title: str, orgs: list[Organization]) -> str:
        if not orgs:
            return f"{title}: []\n"
        return f"{title}:\n- " + "\n- ".join(str(o) for o in orgs) + "\n"

    @staticmethod
    def _format_organizations_map(mapping: Iterable[tuple[list[Any], str]]) -> str:
        ret = ""
        for group, map_to in mapping:
            group = [str(item) for item in group]
            max_child_len = max(len(org) for org in group)
            ret += f"{group[0]:<{max_child_len}}  ---> {map_to}\n"
            for org in group[1:-1]:
                ret += f"{org:<{max_child_len}}  -|\n"
            if len(group) > 1:
                ret += f"{group[-1]:<{max_child_len}}  -/\n"
        return ret or "[] (all resolved)"

    @staticmethod
    def _merge_roots(existing: dict[str, Match], new: dict[str, Match]) -> dict[str, Match]:
        for ror_id, org in new.items():
            if ror_id in existing:
                existing[ror_id].merge(org)
            else:
                existing[ror_id] = org
        return existing

    @classmethod
    def _resolve_parents(cls, grouped_children: dict[str, list[Match]], parents: dict[str, Organization | Exception],
                         existing_roots: dict[str, Match],
                         root_follow: Iterable[str]) -> tuple[dict[str, Match], list[Match]]:
        statistic_for_log: list[tuple[list[Match], str]] = []
        may_new_roots: list[Match] = []
        forks: list[Match] = []
        for parent_id, parent_or_exc in parents.items():
            children = grouped_children[parent_id]
            if isinstance(parent_or_exc, Exception):
                statistic_for_log.append((children, f"❌ {parent_id} ({type(parent_or_exc).__name__}"))
                may_new_roots.extend(children)
            elif not any(t in root_follow for t in parent_or_exc.types):
                statistic_for_log.append(
                    (children, f"↩️ {parent_id} {parent_or_exc.types} not in any of {tuple(root_follow)}")
                )
                may_new_roots.extend(children)
            elif not parent_or_exc.is_active:
                statistic_for_log.append((children, f"↩️ {parent_id} not activate"))
                may_new_roots.extend(children)
            else:
                parent = Match.merge_organization(parent=parent_or_exc, children=children)
                statistic_for_log.append((children, f"✅ {parent}"))
                (forks if parent_or_exc.parent else may_new_roots).append(parent)
        logged_map: list[tuple[list, str]] = []
        for group, parent in statistic_for_log:
            logged_map.append(([item for item in group], parent))
        logging.info(f"Resolved parent relationships:\n{cls._format_organizations_map(logged_map)}")
        for match in may_new_roots:
            if match.organization.id in existing_roots:
                existing_roots[match.organization.id].merge(match)
            else:
                existing_roots[match.organization.id] = match
        return existing_roots, forks

    async def fetch_one(self, session: ClientSession, ror_id: str) -> Organization:
        """Fetch one record from ROR."""
        id_str = ror_id.split("/")[-1]
        url = self._escape("/v2/organizations/{id}", id=id_str)
        ret = await self.__request_with_retry(session, "GET", url, out_model=Organization,
                                              usage_for_log=f"Fetch {ror_id}")
        logging.info(f"Fetch {ror_id} ends with record: {ret}")
        return ret

    async def match(self, organization_name: str,
                    find_root=True, root_follow: Iterable[str] = frozenset(["education", "company"]),
                    follow_not_chosen=False, min_follow_score: float = None) -> tuple[list[Match], list[Match]]:
        """Trying match the given `organization_name` into some ROR record and resolved to their root organization.
        Returns a tuple of (first match, resolved result).
        """
        async with self._create_session() as session:
            first_match = await self.match_request(session, organization_name)
            for match in first_match:
                _ror_cache[match.organization.id] = match.organization
            if not find_root:
                return first_match, first_match
            return first_match, await self._find_root_nodes(first_match, organization_name, root_follow,
                                                            follow_not_chosen, min_follow_score, session)

    async def match_one_or_origin(
            self, organization_name: str,
            find_root=True, root_follow: Iterable[str] = frozenset(["education", "company"]),
            follow_not_chosen=False, min_follow_score: float = None, llm: BaseChatModel = None) -> str:
        """Trying match the given `organization_name` into one ROR record and return the origin name if failed."""
        try:
            matches = await self.match(organization_name, find_root, root_follow, follow_not_chosen, min_follow_score)
        except Exception as e:
            logging.error(f"Matching {organization_name!r} failed with Exception and returns its origin name: {e}",
                          exc_info=True)
            return organization_name
        origin_match, final_match = matches
        if len(final_match) != 1:
            if not any(match.organization.ror_name == organization_name for match in final_match):
                if llm:
                    return await self._match_by_llm(origin_match, organization_name, llm, root_follow)
                logging.warning(f"Matching {organization_name!r} with {len(matches)} results (expected to be 1) "
                                "and returns its origin name.")
            return organization_name
        return final_match[0].organization.ror_name

    async def match_request(self, session: ClientSession, name: str) -> list[Match]:
        """Make a simple request to ROR and returns its raw result."""
        url = self._escape("/v2/organizations?affiliation={name}", name=name)
        all_records = (await self.__request_with_retry(session, "GET", url, out_model=RORMatchResponse,
                                                       usage_for_log=f"Match {name!r}")).items
        ret = []
        log_str = f"Match {name!r} got {len(all_records)} results:"
        for match in all_records:
            log_str += f"\n- ({match}"
            if match.organization.is_active:
                ret.append(match)
        if not len(all_records):
            log_str += " []"
        logging.info(log_str)
        return ret

    def _create_session(self) -> ClientSession:
        return ClientSession(base_url=self.api_base, timeout=ClientTimeout(connect=10, sock_read=20), trust_env=True)

    def _extract_parents(self, children: list[Match], query: str, depth: int,
                         follow_not_chosen=False, min_follow_score: float = None
                         ) -> tuple[dict[str, list[Match]], dict[str, Match]]:
        """Returns a dict meaning (parent.id, child organizations) and a list of root organizations."""
        groups: dict[str, list[Match]] = defaultdict(list)
        parents: dict[str, Relationship] = {}
        root_nodes: list[Match] = []
        dropped: list[Match] = []
        for item in children:
            if not item.chosen:
                if (not follow_not_chosen) or (min_follow_score is not None and item.score < min_follow_score):
                    dropped.append(item)
                    continue
            if not item.organization.parent:
                root_nodes.append(item)
                continue
            groups[item.organization.parent.id].append(item)
            parents[item.organization.parent.id] = item.organization.parent

        # codes for log
        log_str = f"Query {query!r} and resolving parent relation ship for the {depth} time.\n"
        if not dropped:
            log_str += "Dropped: []\n"
        else:
            log_str += f"Dropped:\n- " + "\n- ".join(f"({o.score}) {o.organization}" for o in dropped) + "\n"
        if root_nodes:
            log_str += "Root nodes:\n- " + "\n- ".join(str(match.organization) for match in root_nodes) + "\n"

        log_str += "Relationships:\n"
        mapping: list[tuple[list, str]] = []
        for parent in sorted(parents.values(), key=lambda p: p.label):
            orgs = [match.organization for match in groups[parent.id]]
            map_to = f"{'⬇️' if parent.id not in _ror_cache else '✅'}{parent.id} ({parent.label!r})"
            mapping.append((orgs, map_to))
        log_str += self._format_organizations_map(mapping)

        logging.info(log_str)
        return groups, {match.organization.id: match for match in root_nodes}

    async def _fetch_records(self, session: ClientSession,
                             ror_ids: Iterable[str]) -> dict[str, Organization | BaseException]:
        ror_ids = set(ror_ids)
        # load to local variable: fetch_records may update cache
        existing: dict[str, Organization | Exception] = {id_: _ror_cache.get(id_) for id_ in ror_ids}
        existing = {k: v for k, v in existing.items() if v is not None}
        if existing:
            logging.info(f"These ROR items are cached: {list(existing)}")
        miss_ids: list[str] = list(set(ror_ids) - set(existing))
        if not miss_ids:
            return existing

        records = await asyncio.gather(*(self.fetch_one(session, id_) for id_ in miss_ids), return_exceptions=True)
        for id_, record in zip(miss_ids, records):
            if isinstance(record, self.RateLimit):
                raise record
            elif isinstance(record, Organization):
                _ror_cache[record.id] = record
            # other exception pass to caller
            existing[id_] = record
        return existing

    async def _find_root_nodes(self, first_match: list[Match], organization_name: str, root_follow: Iterable[str],
                               follow_not_chosen: bool, min_follow_score: float, session: ClientSession) -> list[Match]:
        relations, roots = self._extract_parents(
            first_match, query=organization_name, depth=1, follow_not_chosen=follow_not_chosen,
            min_follow_score=min_follow_score
        )
        existing_parents = await self._fetch_records(session, relations)

        roots, forks = self._resolve_parents(relations, existing_parents, roots, root_follow)
        depth = 2
        while forks:
            parent_relations, new_roots = self._extract_parents(
                forks, query=organization_name, depth=depth,
                follow_not_chosen=follow_not_chosen, min_follow_score=min_follow_score
            )
            roots = self._merge_roots(roots, new_roots)
            new_parents = await self._fetch_records(session, parent_relations)
            roots, forks = self._resolve_parents(parent_relations, new_parents, roots, root_follow)
        return list(roots.values())

    async def _match_by_llm(self, first_match: list[Match], org_name: str, llm: BaseChatModel,
                            root_follow: Iterable[str]) -> str:
        from langfuse.langchain import CallbackHandler
        langfuse_handler = CallbackHandler()
        async with self._create_session() as session:
            inputs = _MatchByLLMInput(first_match=first_match, org_name=org_name)
            input_as_configs = RunnableConfig(configurable=dict(llm=llm, session=session, client=self))
            try:
                org: Organization = await (
                    _match_by_llm
                    .with_config(run_name="match_ROR_by_LLM", callbacks=[langfuse_handler])
                    .ainvoke(inputs, config=input_as_configs)
                )
            except RORException:
                return org_name
            as_match = Match.merge_organization(org, [])
            as_match.chosen = True
            as_match.score = 1.
            roots = await self._find_root_nodes([as_match], org_name, root_follow, follow_not_chosen=True,
                                                min_follow_score=0., session=session)
            return roots[0].organization.ror_name

    async def __request_with_retry(self, session: ClientSession, method: str, path_with_query: str,
                                   out_model: Type[_Model], usage_for_log: str) -> _Model:
        """Success msgs is not logged."""
        last_exception: Exception = RuntimeError(f"Unknown exception when {usage_for_log} from ROR.")
        for retry_count in range(1, self.max_retry_per_request + 1):
            try:
                response = await session.request(method, url=self.api_base + path_with_query, headers=self._headers,
                                                 ssl=self.verify_ssl)
                if response.status == 429:  # HTTP Too Many Requests
                    raise RORClient.RateLimit(bool(self.client_id))
                response.raise_for_status()
                return out_model.model_validate(await response.json())
            except RORClient.RateLimit:
                raise
            except Exception as e:
                last_exception = e
                logging.error(f"Failed to {usage_for_log} from ROR for the {retry_count} time with "
                              f"{type(e).__name__}: {e}", exc_info=True)
        logging.error(f"Failed to {usage_for_log} for too many times ({self.max_retry_per_request})!"
                      " Aborted with last exception.")
        raise last_exception


class RORException(RuntimeError):
    """A flag that known Exception handled in inner code."""


class _LLMSelectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ror_id: str = None


class _MatchByLLMInput(TypedDict):
    first_match: list[Match]
    org_name: str

    # These inputs are in configurable of config
    # llm: instance of BaseChatModel
    # session: instance of ClientSession
    # client: instance of RORClient


@entrypoint()
async def _match_by_llm(inputs: _MatchByLLMInput, config: RunnableConfig) -> Organization:
    """Return an organization matched by LLM with retry."""
    first_match = inputs["first_match"]
    org_name = inputs["org_name"]
    llm: BaseChatModel = config["configurable"]["llm"]
    session: ClientSession = config["configurable"]["session"]
    client: RORClient = config["configurable"]["client"]

    sub_config = RunnableConfig(configurable=_ToolConf(ror_client=client, ror_session=session))
    agent = create_agent(llm, tools=[_ror_search], system_prompt=_MATCH_ONE_ROR_SYS_PROMPT_TEXT)
    references = json.dumps([match.organization.simplified_dump for match in first_match], ensure_ascii=False, indent=2)

    max_retry = 3
    for _ in range(max_retry):
        try:
            out_msgs = await agent.ainvoke(
                {"messages": [
                    {
                        "role": "user",
                        "content": f"""## Reference Records\n\n{references}\n\n## Target Organization\n\n{org_name}"""
                    }
                ]}, config=sub_config)
            out_text = out_msgs["messages"][-1].content

            left = out_text.find("{")
            right = out_text.rfind("}")
            if left == -1 or right == -1:
                logging.error(f"LLM生成的{out_text!r}不包含完整的json对象")
                raise RORException("查询机构ROR信息时发生异常")
            json_text = out_text[left:right+1]
            try:
                out: _LLMSelectResult = _LLMSelectResult.model_validate_json(json_text)
            except ValidationError as e:
                logging.error(f"查询机构信息时发生异常：{e}。LLM完整输出为：{out_text!r}，其中识别到的json内容为{json_text!r}")
                continue

            if not out.ror_id:
                logging.warning(f"LLM match {org_name} returns nothing.")
                continue
            return await client.fetch_one(session, out.ror_id)
        except Exception as e:
            logging.error(f"Matching {org_name!r} failed with unknown {type(e).__name__}: {e}", exc_info=True)
    logging.warning(f"Try matching {org_name!r} by LLM failed for too many times ({max_retry}), returns origin.")
    raise RORException(f"Failed for too many times ({max_retry}")


class _ToolConf(TypedDict):
    ror_client: RORClient
    ror_session: ClientSession


@tool("ror_search", parse_docstring=True, error_on_invalid_docstring=True)
async def _ror_search(org_name: str, config: RunnableConfig) -> str:
    """Search `org_name` to match recorded organization name in ROR database.

    Args:
        org_name: str, the target organization name.

    Returns:
        Matched results with their recorded name, aliases, country (in ISO3166-1 Alpha2 code) and ROR ID in a list.
    """
    client: RORClient = config["configurable"]["ror_client"]
    session: ClientSession = config["configurable"]["ror_session"]
    matches = await client.match_request(session, org_name)
    result = [match.organization.simplified_dump for match in matches]
    return json.dumps(result, ensure_ascii=False, indent=2)


_MATCH_ONE_ROR_SYS_PROMPT_TEXT = """## Role
You are an Academic Affiliation Retrieval Expert.  
Your task is to find an organization record that represents the same organization as the the name \
provided by the user, (or a parent organization of the one that user is inquiring about) based on \
"ror_name" and "aliases", and return its ROR ID.

## Task
1. Check whether any organization in the references list of ROR organizations (based on "ror_name" and "aliases") \
matches the target organization that the user is inquiring about. If so, return its ROR ID directly.
2. If none of the existing references meet the wanted organization, call tool "ror_search" for a search, and perform \
further matching based on the search results.
3. If tool call fails, or if no matching organization record is found after more than 3 tool calls, stop and return \
an empty JSON.

## Notice
If the target organization is a multinational entity and there are existing records of its branches in other \
regions, you can still regard the record as a successful match and return its ROR ID.
If there are multiple branches of the organization in the records, you only need to output ROR ID of any one of them.
However, if there is a headquarters, you should directly output the ROR ID of the headquarters.

## Output Format
Return your answer strictly following this JSON structure:

{{
    "ror_id": "",
}}

---

## Example 1

    ### Input
    target: "Huawei Cloud"
    references: [
        {{"ror_name": "Huawei Technologies (Poland)", "ror_id": "https://ror.org/007a2ta87"}},
        {{"ror_name": "Huawei Technologies (Sweden)", "ror_id": "https://ror.org/0500fyd17"}}
    ]

    ### Output ("Huawei Cloud" is subsidiary of "Huawei Technologies" and has a record of being its Polish branch)
    {{
        "ror_id": "https://ror.org/007a2ta87"
    }}

## Example 2

    ### Input
    target: "HUAWEI"
    references: []

    ### Tool Output on "Huawei"
    references: [
        {{"ror_name": "Huawei Technologies (Poland)", "ror_id": "https://ror.org/007a2ta87"}},
        {{"ror_name": "Huawei Technologies (China)", "ror_id": "https://ror.org/00cmhce21"}}
    ]

    ### Output ("China" is headquarters of "Huawei Technologies")
    {{
        "ror_id": "https://ror.org/00cmhce21"
    }}
"""
