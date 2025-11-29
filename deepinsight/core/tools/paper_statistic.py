import json
import logging
import os
from typing import ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, RootModel
from sqlalchemy import Select, and_, desc, distinct, or_, select, func

from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.databases.connection import Database
from deepinsight.databases.models.academic import Author, Conference, Paper, PaperAuthorRelation
from deepinsight.core.utils.mcp_utils import MCPClientUtils


class CountItem(BaseModel):
    name: str
    count: int

    def __init__(self, name: str, count: int):
        super().__init__(name=name, count=count)


class CoAuthorshipLine(BaseModel):
    name1: str
    email1: str
    affiliation1: str

    name2: str
    email2: str
    affiliation2: str

    count: int


class AuthorsPaperLine(BaseModel):
    name: str
    email: str
    affiliation: str
    all_count: int


class FirstAuthorLine(AuthorsPaperLine):
    first_count: int


class _BaseAnalysisResult(BaseModel):
    title: ClassVar[str]
    figure_title: ClassVar[str]

    total_count: int
    """Paper count of current conference."""
    top_table: list[CountItem]
    """`Other` is not included in this table."""

    figure_url: str
    """A generated table image binary URL containing data from top_table."""


class DomainAnalysisResult(_BaseAnalysisResult):
    title: ClassVar[str] = "【统计分析】论文主题领域分析"
    figure_title: ClassVar[str] = "论文Top主题领域"


class AffiliationAnalysisResult(_BaseAnalysisResult):
    title: ClassVar[str] = "【统计分析】论文投稿机构分析"
    figure_title: ClassVar[str] = "论文投稿Top机构"

    unknown_count: int
    """Number of authors which has no affiliation info."""


class CountryAnalysisResult(_BaseAnalysisResult):
    title: ClassVar[str] = "【统计分析】论文投稿国家分析"
    figure_title: ClassVar[str] = "论文投稿Top国家"

    unknown_count: int
    """Number of authors which has no affiliation country info."""


class _TranslateResult(BaseModel):
    items: list[CountItem]
    count_on: str
    title: str


@tool(parse_docstring=True)
async def country_analysis(conf_name: str, conf_year: int, user_lang: str,
                           config: RunnableConfig, top_n: int = 15) -> dict:  # noqa: `config` not visible for LLM
    """用于获取知识库中已记录的会议中，提交论文最多的`top_n`个国家和地区的提交数量信息，和未注明国籍信息的作者总数`unknown_count`。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        user_lang: str, 用户提问时使用的语言。
        top_n: int, 最多返回的结果数目。

    Returns:
        包括此次会议已记录的论文总数、前top_n个国家和地区的提交数量详情，及
        以上述数据绘制的柱状图链接`figure_url`，和未注明国籍信息的作者总数`unknown_count`。
    """
    with Database().get_session() as session:  # type: Session
        conf = _conference_of(conf_name, conf_year)
        total_count = session.query(Paper).filter(Paper.conference_id.in_(conf)).count()
        country_missing_count = (
            session.query(func.count(distinct(PaperAuthorRelation.author_id)))
            .join(Paper, Paper.paper_id == PaperAuthorRelation.paper_id)
            .join(Author, Author.author_id == PaperAuthorRelation.author_id)
            .filter(Paper.conference_id.in_(conf))
            .filter(or_(Author.affiliation_country.is_(None), Author.affiliation_country == ""))
        ).scalar()
        # noinspection PyTestUnpassedFixture
        top_countries = [
            CountItem(country, count) for (country, count) in (
                session.query(Author.affiliation_country, func.count(distinct(Paper.paper_id)))
                .join(PaperAuthorRelation, PaperAuthorRelation.paper_id == Paper.paper_id)
                .join(Author, Author.author_id == PaperAuthorRelation.author_id)
                .filter(Paper.conference_id.in_(conf))
                .filter(Author.affiliation_country.isnot(None))
                .filter(Author.affiliation_country != "")  # type: ignore
                .group_by(Author.affiliation_country)
                .order_by(func.count(distinct(Paper.paper_id)).desc())
                .limit(top_n)
            ).all()
        ]
    figure_url, top_countries = await _generate_chart_with_translation(config, user_lang, top_countries, "国家及地区",
                                                                       title=CountryAnalysisResult.figure_title)

    return CountryAnalysisResult(
        total_count=total_count, top_table=top_countries,
        figure_url=figure_url, unknown_count=country_missing_count
    ).model_dump()


@tool(parse_docstring=True)
async def domain_analysis(conf_name: str, conf_year: int, user_lang: str,
                          config: RunnableConfig, top_n: int = 15) -> dict:  # noqa: `config` not visible for LLM
    """用于获取知识库中已记录的会议中，提交论文最多的`top_n`个关键词和出现数量信息。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        user_lang: str, 用户提问时使用的语言。
        top_n: int, 最多返回的结果数目。超过此数目的其他关键词将不出现在结果中。

    Returns:
        包括此次会议已记录的论文总数、出现次数位于前top_n的关键词及其数量，及
        以上述数据绘制的柱状图链接`figure_url`。
    """
    if os.environ.get("CONFERENCE_DOMAIN_ANALYSIS_USING_TOPICS") != "1":
        return await _domain_analysis_v1(conf_name, conf_year, user_lang, config, top_n)
    with Database().get_session() as session:  # type: Session
        conf = _conference_of(conf_name, conf_year)
        total_count = session.query(Paper).filter(Paper.conference_id.in_(conf)).count()
        topics: list[str] = session.execute(
            select(Conference.topics).where(Conference.conference_id.in_(conf))
        ).scalar()
        if not topics:
            raise RuntimeError("该会议未记录Topic信息")
        count_result = session.execute(
            select(Paper.topic, func.count(Paper.paper_id))
            .where(and_(Paper.conference_id.in_(conf), Paper.topic.in_(topics)))
            .group_by(Paper.topic)
        ).all()
        null_count = session.execute(
            select(func.count(Paper.paper_id))
            .where(and_(Paper.conference_id.in_(conf), Paper.topic.is_(None)))
        ).scalar()

    sorted_frequency = sorted(
        (CountItem(topic, count) for topic, count in count_result),
        key=lambda item: item.count, reverse=True
    )[:top_n]
    other_count = total_count - sum(item.count for item in sorted_frequency) - null_count
    sorted_frequency += [CountItem("Unknown", null_count), CountItem("Other", other_count)]

    figure, sorted_frequency = await _generate_chart_with_translation(config, user_lang, sorted_frequency,
                                                                      count_on="Topic",
                                                                      title=DomainAnalysisResult.figure_title)
    return DomainAnalysisResult(total_count=total_count, top_table=sorted_frequency, figure_url=figure).model_dump()


async def _domain_analysis_v1(conf_name: str, conf_year: int, user_lang: str,
                              config: RunnableConfig, top_n: int = 15) -> dict:
    """Implements as keyword frequency analysis."""
    with Database().get_session() as session:  # type: Session
        conf = _conference_of(conf_name, conf_year)
        total_count = session.query(Paper).filter(Paper.conference_id.in_(conf)).count()
    query_tools = await MCPClientUtils.get_tools(tools_name_list=["get_proceedings_keyword_frequency"],
                                                 server_name="conference-static")
    if not query_tools:
        raise RuntimeError("无法加载统计查询工具。")
    get_proceedings_keyword_frequency = query_tools[0]
    frequencies: dict[str, int] = json.loads(await get_proceedings_keyword_frequency.ainvoke(
        dict(year_range=[conf_year, conf_year], conferences=conf_name)
    ))["result"]["keyword_frequency"]
    sorted_frequency = sorted(
        (CountItem(k, v) for (k, v) in frequencies.items()),
        key=lambda item: item.count, reverse=True
    )[:top_n]

    figure, sorted_frequency = await _generate_chart_with_translation(config, user_lang, sorted_frequency,
                                                                      count_on="关键词",
                                                                      title=DomainAnalysisResult.figure_title)
    return DomainAnalysisResult(total_count=total_count, top_table=sorted_frequency, figure_url=figure).model_dump()


@tool(parse_docstring=True)
async def affiliation_analysis(conf_name: str, conf_year: int, user_lang: str,
                               config: RunnableConfig, top_n: int = 15):  # noqa: `config` not visible for LLM
    """用于获取知识库中已记录的会议中，提交论文最多的`top_n`个机构的提交数量信息，和未注明所在机构信息的作者总数`unknown_count`。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        user_lang: str, 用户提问时使用的语言。
        top_n: int, 最多返回的结果数目。

    Returns:
        包括此次会议已记录的论文总数、发表论文最多的前top_n个机构的提交数量详情及以该数据绘制的柱状图链接`figure_url`，
        和未注明所在机构信息的作者总数`unknown_count`。
    """
    query_tools = await MCPClientUtils.get_tools(tools_name_list=["get_institution_stats"],
                                                 server_name="conference-static")
    if not query_tools:
        raise RuntimeError("无法加载统计查询工具。")
    get_institution_stats = query_tools[0]
    result = json.loads(await get_institution_stats.ainvoke(
        dict(top_n=top_n, include_coauthorship=True, conferences=conf_name, year=conf_year)
    ))
    institutions = [CountItem(name=row["name"], count=row["count"]) for row in result["result"]["institution_stats"]]

    with Database().get_session() as session:  # type: Session
        conf = _conference_of(conf_name, conf_year)
        total_count = session.query(Paper).filter(Paper.conference_id.in_(conf)).count()
        unknown_count = (
            session.query(func.count(distinct(Author.author_id)))
            .join(PaperAuthorRelation, Author.author_id == PaperAuthorRelation.author_id)
            .join(Paper, Paper.paper_id == PaperAuthorRelation.paper_id)
            .filter(Paper.conference_id.in_(conf))
            .filter(or_(Author.affiliation.is_(None), Author.affiliation == ""))
        ).scalar()

    figure, institutions = await _generate_chart_with_translation(config, user_lang, institutions, "机构",
                                                                  title=AffiliationAnalysisResult.figure_title)
    return AffiliationAnalysisResult(total_count=total_count, top_table=institutions,
                                     figure_url=figure, unknown_count=unknown_count).model_dump()


@tool(parse_docstring=True)
async def co_authorship_analysis(conf_name: str, conf_year: int, config: RunnableConfig, top_n: int = 10) -> list[dict]:
    """用于获取知识库中已记录的会议中，合作次数最多的`top_n`组作者的信息及合作次数。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        top_n: int, 最多返回的结果数目。超过此数目的不会出现在结果中。

    Returns:
        一个长度为top_n的列表。每个列表项的 name1/email1/affiliation1 和 name2/email2/affiliation2 为合作双方的信息，
        count为双方的合作次数。
    """
    query_tools = await MCPClientUtils.get_tools(tools_name_list=["get_author_coauthorship"],
                                                 server_name="conference-static")
    if not query_tools:
        raise RuntimeError("无法加载统计查询工具。")
    get_author_coauthorship = query_tools[0]
    origin_co_authorship = json.loads(await get_author_coauthorship.ainvoke(
        dict(top_n=top_n, conferences=conf_name, year=conf_year)
    ))["result"]["author_coauthorship"]
    results = []
    for record in origin_co_authorship:
        a: dict = record["author1"]
        b: dict = record["author2"]
        results.append(CoAuthorshipLine(
            name1=a.get("name") or "", email1=a.get("email") or "N/A", affiliation1=a.get("affiliation") or "",
            name2=b.get("name") or "", email2=b.get("email") or "N/A", affiliation2=b.get("affiliation") or "",
            count=record["collaboration_count"]
        ))
    return _dump_list(sorted(results, key=lambda item: item.count, reverse=True))


@tool(parse_docstring=True)
async def first_author_analysis(conf_name: str, conf_year: int, config: RunnableConfig, top_n: int = 10) -> list[dict]:
    """用于获取知识库中已记录的会议中，以第一作者（不含共同第一作者）身份发表论文次数最多的`top_n`位作者的论文发布信息。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        top_n: int, 最多返回的作者数。超过此数目的不会出现在结果中。

    Returns:
        一个长度为top_n的列表。每个列表项的 name/email/affiliation 为作者的身份信息，first_count为该作者在本次会议以第一作者身份
        发表的论文总数，all_count为该作者在本次会议以任意作者身份（包括通讯作者）的论文次数。
    """
    paper_ids = select(Paper.paper_id).where(Paper.conference_id.in_(_conference_of(conf_name, conf_year)))
    with Database().get_session() as session:  # type: Session
        first_author_count = session.execute(
            select(
                PaperAuthorRelation.author_id,
                func.count(distinct(PaperAuthorRelation.paper_id)).label("first_cnt")
            )
            .where(and_(PaperAuthorRelation.paper_id.in_(paper_ids), PaperAuthorRelation.author_order == 1))
            .group_by(PaperAuthorRelation.author_id)
            .order_by(desc("first_cnt"))
            .limit(top_n)
        ).all()
        author_ids = [row[0] for row in first_author_count]
        all_paper_count = session.execute(
            select(
                PaperAuthorRelation.author_id,
                func.count(distinct(PaperAuthorRelation.paper_id)).label("total_cnt")
            )
            .where(and_(PaperAuthorRelation.paper_id.in_(paper_ids), PaperAuthorRelation.author_id.in_(author_ids)))
            .group_by(PaperAuthorRelation.author_id)
        ).all()
        author_info = session.execute(
            select(Author.author_id, Author.author_name, Author.email, Author.affiliation)
            .where(Author.author_id.in_(author_ids))
        ).all()

    first_author_dict = {k: v for k, v in first_author_count}
    all_paper_dict = {k: v for k, v in all_paper_count}
    author_info_dict = {id_: dict(name=name, email=email or "N/A", affiliation=af) for (id_, name, email, af) in author_info}

    results = [
        FirstAuthorLine(**author_info_dict[id_], first_count=first_author_dict[id_], all_count=all_paper_dict[id_])
        for id_ in author_ids
    ]
    return _dump_list(sorted(results, key=lambda item: (-item.first_count, -item.all_count, item.name)))


@tool(parse_docstring=True)
async def authors_paper_analysis(conf_name: str, conf_year: int, config: RunnableConfig, top_n: int = 10) -> list[dict]:
    """用于获取知识库中已记录的会议中，在指定会议上发表论文篇数最多的`top_n`位作者的信息与文章数。

    Args:
        conf_name: str, 指定会议的官方缩写，比如"POPL".
        conf_year: int, 指定的会议年份，使用公元年份，比如2025.
        top_n: int, 最多返回的作者数。超过此数目的不会出现在结果中。

    Returns:
        一个长度为top_n的列表。每个列表项的 name/email/affiliation 为作者的身份信息，
        all_count为该作者在本次会议以任意作者身份（包括通讯作者）的论文次数。
    """
    paper_ids = select(Paper.paper_id).where(Paper.conference_id.in_(_conference_of(conf_name, conf_year)))
    with Database().get_session() as session:  # type: Session
        author_paper_count = session.execute(
            select(
                PaperAuthorRelation.author_id,
                func.count(distinct(PaperAuthorRelation.paper_id)).label("all_cnt")
            )
            .where(PaperAuthorRelation.paper_id.in_(paper_ids))
            .group_by(PaperAuthorRelation.author_id)
            .order_by(desc("all_cnt"))
            .limit(top_n)
        ).all()
        author_ids = [row[0] for row in author_paper_count]
        author_info = session.execute(
            select(Author.author_id, Author.author_name, Author.email, Author.affiliation)
            .where(Author.author_id.in_(author_ids))
        ).all()

    all_paper_dict = {k: v for k, v in author_paper_count}
    author_info_dict = {id_: dict(name=name, email=email or "N/A", affiliation=af) for (id_, name, email, af) in author_info}

    results = [AuthorsPaperLine(**author_info_dict[id_], all_count=all_paper_dict[id_]) for id_ in author_ids]
    return _dump_list(sorted(results, key=lambda item: (-item.all_count, item.name)))


def _conference_of(conference_short_name: str | None, year: int) -> Select[tuple[str]]:
    return (
        select(Conference.conference_id)
        .where(and_(Conference.short_name.ilike(conference_short_name), Conference.year == year))
    )


async def _generate_chart_with_translation(config: RunnableConfig, target_lang: str, items: list[CountItem],
                                           count_on: str,
                                           title: str) -> tuple[str, list[CountItem]]:
    chart_tools = await MCPClientUtils.get_tools(tools_name_list=["generate_bar_chart"], server_name="mcp-chart")
    if not chart_tools:
        raise RuntimeError("无法加载绘图工具。")
    generate_bar_chart = chart_tools[0]
    items, count_on, title = await _translate_figure(config, target_lang, items, count_on, title)
    datas = [dict(category=item.name, value=item.count) for item in items]
    if not datas:
        return "", items
    datas = sorted(datas, key=lambda item: item["value"], reverse=True)
    call_result = await generate_bar_chart.ainvoke(dict(
        title=title, data=datas, axisYTitle="论文数量", axisXTitle=count_on, height=1200, width=1200
    ))
    # 图表工具统一返回字典结构，包含键 "png_path"
    try:
        call_result = json.loads(call_result)
    except json.JSONDecodeError:
        logging.error(f"图表工具返回的结果不是JSON格式：{call_result}")
        return "", items
    png_path = call_result.get("png_path") if isinstance(call_result, dict) else ""
    return png_path, items


async def _translate_figure(config: RunnableConfig, target_lang: str,
                            items: list[CountItem], count_on: str, title: str) -> tuple[list[CountItem], str, str]:
    rc = parse_research_config(config)
    llm: BaseChatModel = rc.default_model
    request_obj = _TranslateResult(items=items, count_on=count_on, title=title).model_dump_json()
    request = (
        f"将以下JSON对象中所有属性的值翻译为{target_lang}，且所有属性的key的语言保持不变。返回翻译后的json对象，不要返回额外内容。\n"
        f"如果待翻译的字符串为人物名称且无官方翻译，则跳过该名称的翻译。\n"
        f"如果待翻译的字符串为国际知名机构名且在目标语言已有标准化缩写，则使用其缩写作为结果。\n"
        f"{request_obj}")
    max_retry = 3
    for retried in range(max_retry):
        try:
            response = (await llm.ainvoke(request)).content
            first = response.find("{")
            last = response.rfind("}")
            if first == -1 or last == -1:
                logging.error(f"翻译表格数据到目标语言{target_lang}时LLM未生成JSON")
                continue
            response = response[first:last + 1]
            out = _TranslateResult.model_validate_json(response)
            return out.items, out.count_on, out.title
        except Exception as e:
            logging.error(f"翻译表格数据到目标语言{target_lang}时出现错误{type(e).__name__}: {e}")
    return items, count_on, title


def _dump_list(any_list: list[BaseModel]) -> list[dict]:
    return RootModel(any_list).model_dump()
