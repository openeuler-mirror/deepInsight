"""Conference-Paper MCP server definition."""
import json
import os
import sys
from collections import defaultdict
import re
from typing import Literal, Any, Optional

import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from sqlalchemy import and_, desc, or_, select, func, Select
from sqlalchemy.orm import aliased, Session

from deepinsight.config.database_config import DatabaseConfig
from deepinsight.databases.connection import Database
from deepinsight.databases.models.academic import Author, Conference, Paper, PaperAuthorRelation

MCP_TRANSPORT: Literal["stdio", "sse", "streamable-http"] = "stdio"
mcp_app = FastMCP(name="Conference-Paper MCP Server")


@mcp_app.tool()
def get_author_coauthorship(
        conferences: str,
        top_n: int = 50,
        filter_institution: Optional[str] = None,

        year: int = 2025) -> dict[str, Any]:
    """
    发现指定顶会论文集中作者的合作关系网络

    ## 适用场景
    - 识别核心研究团队与学术领袖
    - 分析机构内部或跨机构合作模式
    - 支持学术生态可视化与人才图谱构建

    ## 参数策略
    filter_institution:
    - 空值：分析全局作者合作关系
    - 指定机构：仅分析该机构内部或与外部合作
    top_n:
    - 返回合作频次最高的 Top N 组作者对

    ## 组合调用路径示例
    【核心学者识别】
    1. get_proceedings_keyword_frequency：确定 "formal verification" 领域关键词
    2. 本工具：发现该领域高频合作作者对
    3. get_institution_stats：定位其所属机构

    【企业研发团队分析】
    1. 本工具：filter_institution="Cadence, Inc."，分析其内部合作网络
    2. 识别核心研发 leader 与协作模式

    ## 输出结构示例
    {
    "coauthorships": [
        {
            "author1": {
                "name": "Yuan Xie",
                "affiliation": "University of California, Santa Barbara",
                "email": "yuan@ece.ucsb.edu"
            },
            "author2": {
                "name": "Jie Han",
                "affiliation": "University of Alberta",
                "email": "jie.han@ualberta.ca"
            },
            "collaboration_count": 2,
            "papers": ["Multi-Modal Representation Learning...", "Efficient Parallel Pattern Fault ..."]
        }
    ],
    "filter_institution": null
    }
    """
    results = _get_author_coauthorship_from_db(conferences, year, top_n, filter_institution)
    return {
        "result": {
            "filter_institution": filter_institution,
            "author_coauthorship": results
        }
    }


@mcp_app.tool()
def get_available_proceedings() -> dict[str, Any]:
    """
    获取当前支持分析的顶会论文集列表。同一会议的不同年份视为不同论文集。

    ## 适用场景
    - 用户未指定会议时提供可选列表
    - 跨会议对比分析前的准备步骤
    - 验证用户输入会议是否有效

    ## 参数策略
    - 无输入参数
    - 返回系统当前支持的所有 conferences

    ## 组合调用路径示例
    【跨会议趋势分析】
    1. 本工具：确认可用会议名称
    2. 循环调用 get_proceedings_keyword_frequency 分别分析
    3. 对比不同会议技术焦点差异

    【会议有效性验证】
    1. 用户询问中提及的会议名称为"XXX"
    2. 先调本工具确认是否在列表中。
    3. 若工具返回结果不包含用户指定的会议名，仅包含 ["YYY", "ZZZ"]，提示用户“暂不支持 XXX，可用会议：YYY, ZZZ”。

    【年份有效性验证】
    1. 用户询问中提及的会议名称为"XXX"，期待查询的年份为2025（示例）
    2. 先调本工具确认是否在列表中。
    3. 若工具返回结果包含用户指定的会议名，但仅包含2023/2024年，不包含2025年的数据，提示用户“数据库仅收录 XXX 在2023与2024年的数据”。

    ## 输出结构示例
    {
    "conferences": [
        {"name": "DAC", "year": 2023},
        {"name": "DAC", "year": 2024},
        {"name": "ICCAD", "year": 2025},
        {"name": "DATE", "year": 2025}
    ],
    "count": 5
    }
    """
    conferences = _get_conference_sets_from_db()
    return {
        "conferences": conferences,
        "count": len(conferences)
    }


@mcp_app.tool()
def get_highly_cited_entities(
        conferences: str,
        entity_type: str = "tool",
        top_n: int = 10,

        year: int = 2025
) -> dict[str, Any]:
    """
    识别指定顶会论文集中高频被引用的研究工具或方法

    ## 适用场景
    - 定位指定顶会所属领域的基础设施与核心成果
    - 发现主流工具链（如 Innovus、Genus）影响力
    - 评估开源项目（如 LangGraph）采纳程度

    ## 参数策略
    entity_type:
    - 当前仅支持 "tool"
    top_n:
    - 返回引用次数最高的 Top N 工具

    ## 组合调用路径示例
    【技术成熟度评估】
    1. 用户询问“DAC 2023体现出的技术演进趋势”
    2. 调用本工具：获取 DAC 2023 高频引用工具，假设工具返回包括 SQLite
    3. 调用get_paper_count_by_year：分析 SQLite 使用趋势，并回答用户的问题

    【企业技术影响力分析】
    1. 用户询问“DAC 2023的论文中使用率较高的工具”
    1. 调用本工具（conferences="DAC", YEAR=2023）：提取工具引用频次
    2. 对比工具返回的结果，总结引用情况并回答用户问题

    ## 输出结构示例
    {
    "tools": [
        {"name": "Fusion Compiler", "citation_count": 42},
        {"name": "OpenROAD", "citation_count": 38}
    ],
    "entity_type": "tool",
    "conferences": "DAC",
    "year": 2023
    }
    """

    papers = _get_papers_with_entities_from_db(conferences, year, entity_type)

    # 实体频率统计
    entity_freq = {}

    # 常见工具/框架名称列表（用于演示）
    # 在实际应用中，这可能需要更复杂的 NLP 抽取或其他机制
    known_tools = {
        "comsol", "matlab", "python", "tensorflow", "pytorch", "scikit-learn",
        "keras", "caffe", "torch", "pandas", "numpy", "scipy", "matplotlib",
        "seaborn", "plotly", "opencv", "scrapy", "flask", "django", "fastapi",
        "react", "vue", "angular", "node.js", "express", "spring", "hibernate",
        "mybatis", "redis", "mongodb", "postgresql", "mysql", "sqlite",
        "docker", "kubernetes", "ansible", "jenkins", "git", "github", "gitlab"
    }

    # 统计实体频率
    for paper in papers:
        # 合并标题、摘要等文本内容
        text_content = " ".join([
            paper.get("title") or "",
            paper.get("abstract") or "",
        ]).lower()

        # 检查已知工具
        for tool in known_tools:
            if tool in text_content:
                entity_freq[tool] = entity_freq.get(tool, 0) + 1

        # 使用正则表达式尝试提取其他可能的工具名称
        # 这里只是一个简单的示例，实际应用中可能需要更复杂的 NLP 处理
        # 查找类似 "using XXX" 或 "based on XXX" 的模式
        patterns = [
            r"using\s+([a-zA-Z0-9\-_]+)",
            r"based\s+on\s+([a-zA-Z0-9\-_]+)",
            r"implemented\s+in\s+([a-zA-Z0-9\-_]+)",
            r"written\s+in\s+([a-zA-Z0-9\-_]+)"
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text_content)
            for match in matches:
                # 过滤掉一些常见的非工具词汇
                if match.lower() not in ["the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
                                         "with", "by"]:
                    entity_freq[match.lower()] = entity_freq.get(match.lower(), 0) + 1

    # 转换为列表并排序
    entity_list = [
        {
            "entity": entity,
            "frequency": freq
        }
        for entity, freq in entity_freq.items()
    ]

    # 按频率排序
    entity_list.sort(key=lambda x: x["frequency"], reverse=True)

    # 限制返回数量
    entity_list = entity_list[:top_n]

    # 返回结果
    return {
        "result": {
            "entity_type": entity_type,
            "highly_cited_entities": entity_list
        }
    }


@mcp_app.tool()
def get_institution_stats(
        conferences: str,
        top_n: int = 10,
        include_coauthorship: bool = False,

        year: int = 2025
) -> dict[str, Any]:
    """
    分析指定顶会论文集中机构的科研产出与合作网络

    ## 适用场景
    - 识别领域核心研究机构（高校/企业）
    - 发现跨机构合作模式（如高校-企业联合）
    - 支持地域热点与产学研分析

    ## 参数策略
    include_coauthorship:
    - True：返回机构间合作矩阵（共同发文数）
    - False：仅返回各机构发文量
    top_n:
    - 返回发文量 Top N 的机构（按 count 降序）
    conferences:
    - 可指定会议以分析不同风格（如 DAC 偏工业，ICCAD 偏学术）

    ## 组合调用路径示例
    【产学研合作评估】
    1. 用户询问某会议下体现的产学融合情况
    2. 调用本工具：获取 top_n=15 且 include_coauthorship=True
    3. 过滤出产业机构（如Synopsys, Cadence, NVIDIA），并分析其与高校（如Stanford, UCSD）合作强度

    【机构技术专长定位】
    1. 本工具：获取 Top 机构列表

    ## 输出结构示例
    {
    "institutions": [
        {"name": "Stanford University", "count": 45},
        {"name": "Synopsys, Inc.", "count": 38}
    ],
    "collaboration": [
        {
            "institution1": "Stanford University",
            "institution2": "Synopsys, Inc.",
            "collaboration_count": 5
        }  # Stanford-Synopsys 合作5次
    ] if include_coauthorship else None
    }
    """
    result = {
        "institution_stats": _get_institution_stats_from_db(conferences, year, top_n)
    }
    if include_coauthorship:
        result["collaboration"] = _get_affiliation_relationship_from_db(conferences, year, top_n)
    return {
        "result": result
    }


@mcp_app.tool()
def get_paper_count_by_year(
        filter_keywords: Optional[list[str]] = None,
        filter_affiliation: Optional[str] = None,
        conferences: str = "DAC",
        year: int = 2025
) -> dict[str, Any]:
    """
    按年度统计指定顶会论文集的论文数量，支持关键词/机构过滤

    ## 适用场景
    - 分析该会议所属领域的发展速度
    - 追踪特定技术趋势（如 "Agentic RAG"）
    - 评估机构在当前指定会议的年度产出

    ## 参数策略
    filter_keywords:
    - 支持多关键词 AND 查询（如 ["machine learning", "placement"]）
    - 英文术语优先（如 "P&R" → "placement and routing"）
    - 空值表示不过滤
    filter_affiliation:
    - 机构规范英文名（如 "Tsinghua University", "Synopsys, Inc."）
    conferences:
    - 默认值为 'DAC'，可指定为已知的其他会议名
    year:
    - 单年统计；如需多年趋势，应循环调用或使用 year_range 工具

    ## 组合调用路径示例
    【技术爆发期识别】
    1. 用户询问“根据DAC近五年的论文，是否能判定 Agentic RAG 技术已进入成长期”
    2. 本工具：获取 "Agentic RAG" 在 DAC 2021-2025 每年数量
    3. 计算年增长率，判断是否进入成长期并回答用户问题

    【跨机构对比】
    1. 本工具：获取 MIT 在 "formal verification" 领域产出
    2. 本工具：获取 Cadence 在相同领域产出
    3. 对比学术界与产业界研究重点

    ## 输出结构示例
    {
        "filter_keywords": [
            "NeuralMesh"
        ],
        "filter_affiliation": None,
        "paper_count_by_year": {
            "paper_count": 1,
            "paper_titles": [
            "NeuralMesh: Neural Network For FEM Mesh Generation"
            ]
        }
    }
    """
    # 返回结果
    return {
        "result": {
            "filter_keywords": filter_keywords,
            "filter_affiliation": filter_affiliation,
            "paper_count_by_year": _get_paper_count_from_db(conferences, year, filter_keywords, filter_affiliation)
        }
    }


@mcp_app.tool()
def get_proceedings_keyword_frequency(
        year_range: tuple[int, int],
        conferences: str
) -> dict[str, Any]:
    """
    提取指定顶会论文集在目标年份范围内的关键词分布，通过关键词可以判断论文研究领域

    ## 适用场景
    - 识别当前会议所属领域研究热点（Top20高频词）
    - 发现新兴技术趋势（对比不同时间段）
    - 支撑会议研究主题聚类分析

    ## 参数策略
    year_range:
    - 格式：[起始年, 结束年]，如分析近5年趋势 → (2020, 2024)
    - 建议至少覆盖3年以识别趋势
    conferences:
    - 目标会议名称
    - 可用值参考 get_available_proceedings

    ## 输出结构示例
    {
    "keyword_frequency": {"placement": 45, "machine learning": 38, ...},
    "year_range": [2020, 2024],
    "conferences": "DAC"
    }
    """
    start_year, end_year = year_range
    keyword_freq = _get_keyword_frequency_from_db(conferences, year_range)
    return {
        "result": {
            "conferences": conferences,
            "year_range": {
                "start": start_year,
                "end": end_year
            },
            "keyword_frequency": keyword_freq
        }
    }


def _get_author_coauthorship_from_db(conference_short_name: str, year: int, top_n: int,
                                     filter_institution: Optional[str]) -> list[dict[str, dict | list | int]]:
    with Database().get_session() as session:  # type: Session
        # todo: performance update
        author_query = (
            session.query(
                PaperAuthorRelation.paper_id,
                Author.author_id,
                Author.author_name,
                Author.affiliation,
                Author.email
            )
            .join(Author, PaperAuthorRelation.author_id == Author.author_id)
            .join(Paper, PaperAuthorRelation.paper_id == Paper.paper_id)
            .where(Paper.conference_id.in_(_conference_of(conference_short_name, year)))
        )
        if filter_institution:
            author_query = author_query.filter(Author.affiliation.ilike(f"%{filter_institution}%"))
        authors_with_paper = author_query.all()

        paper_authors: dict[int, list[int]] = defaultdict(list)  # paper_id -> [author_id]
        author_details: dict[int, dict[str, str]] = {}  # author_id -> {name, affiliation, email}

        for row in authors_with_paper:
            paper_id, author_id, name, affiliation, email = row
            if not author_id:
                continue
            paper_authors[paper_id].append(author_id)
            if author_id not in author_details:
                author_details[author_id] = {
                    "name": name,
                    "affiliation": affiliation,
                    "email": email,
                }

        coauthorship_data: dict[tuple[int, int], dict[str, Any]] = {}
        for paper_id, authors in paper_authors.items():
            # 只考虑同一篇论文的作者对
            for i in range(len(authors)):
                for j in range(i + 1, len(authors)):
                    a1, a2 = sorted((authors[i], authors[j]))
                    key = (a1, a2)
                    if key not in coauthorship_data:
                        coauthorship_data[key] = {"count": 0, "papers": []}
                    coauthorship_data[key]["count"] += 1
                    if paper_id not in coauthorship_data[key]["papers"]:
                        coauthorship_data[key]["papers"].append(paper_id)

        result: list[dict[str, Any]] = []
        for (a1_id, a2_id), data in coauthorship_data.items():
            a1_info = author_details.get(a1_id, {})
            a2_info = author_details.get(a2_id, {})
            paper_titles = list(
                session.execute(select(Paper.title).where(Paper.paper_id.in_(data["papers"])))
                .scalars().all()
            )
            result.append(
                {
                    "author1": {
                        "name": a1_info.get("name"),
                        "affiliation": a1_info.get("affiliation"),
                        "email": a1_info.get("email"),
                    },
                    "author2": {
                        "name": a2_info.get("name"),
                        "affiliation": a2_info.get("affiliation"),
                        "email": a2_info.get("email"),
                    },
                    "collaboration_count": data["count"],
                    "papers": paper_titles,  # 共同完成的论文标题列表
                }
            )

        result.sort(key=lambda x: x["collaboration_count"], reverse=True)
        return result[:top_n]


def _get_affiliation_relationship_from_db(conference_short_name: str, year: int,
                                          max_pairs: int) -> list[dict[str, str | int]]:
    """Returns a list of pairs of `(affiliation name, paper id)` from selected conferences."""
    with Database().get_session() as session:  # type: Session
        institution_to_paper_id_view = _institution_to_paper_id_view(conference_short_name, year).subquery()
        alias1 = aliased(institution_to_paper_id_view)
        alias2 = aliased(institution_to_paper_id_view)
        final_query = (
            select(alias1.c.affiliation.label("affiliation1"),
                   alias2.c.affiliation.label("affiliation2"),
                   func.count(alias1.c.paper_id).label("paper_count"))
            .join(alias2, alias1.c.paper_id == alias2.c.paper_id)  # type: ignore
            .where(alias1.c.affiliation < alias2.c.affiliation)
            .group_by(alias1.c.affiliation, alias2.c.affiliation)
            .order_by(func.count(alias1.c.paper_id).desc())
            .limit(max_pairs)
        )
        results = session.execute(final_query).all()
        return [
            {
                "institution1": affiliation1,
                "institution2": affiliation2,
                "collaboration_count": count
            }
            for affiliation1, affiliation2, count in results
        ]


def _get_institution_stats_from_db(conference_short_name: str, year: int, top_n: int):
    with Database().get_session() as session:  # type: Session
        view = _institution_to_paper_id_view(conference_short_name, year).subquery()
        paper_count = session.execute(
            select(view.c.affiliation, func.count(view.c.paper_id).label("paper_count"))
            .where(and_(view.c.affiliation != "", view.c.affiliation.is_not(None)))
            .group_by(view.c.affiliation)
            .order_by(desc("paper_count"))
            .limit(top_n)
        ).all()
        return [dict(name=name, count=count) for (name, count) in paper_count]


def _get_keyword_frequency_from_db(conference_short_name: str, year_range: tuple[int, int]) -> dict[str, int]:
    with Database().get_session() as session:  # type: Session
        conference_view = (
            select(Conference.conference_id)
            .where(
                and_(
                    Conference.short_name.ilike(conference_short_name),
                    Conference.year >= year_range[0],
                    Conference.year <= year_range[1]
                )
            )
        )
        query_results = session.execute(
            select(Paper.keywords).where(Paper.conference_id.in_(conference_view))
        ).all()

        frequencies: dict[str, int] = {}
        for keyword_json in query_results:
            try:
                keywords = json.loads(keyword_json[0])
            except json.JSONDecodeError:
                continue
            for keyword in keywords:
                frequencies[keyword] = frequencies.get(keyword, 0) + 1
        return frequencies


def _get_paper_count_from_db(conference_short_name: str, year: int,
                             filter_keywords: Optional[list[str]] = None,
                             filter_affiliation: Optional[str] = None) -> dict[str, int | list[str]]:
    with Database().get_session() as session:  # type: Session
        conditions = [Paper.conference_id.in_(_conference_of(conference_short_name, year))]
        if filter_affiliation:
            conditions.append(Author.affiliation.ilike(f"%{filter_affiliation}%"))
        if filter_keywords:
            keyword_conditions = []
            for kw in filter_keywords:
                kw = f"%{kw}%"
                keyword_conditions.extend([
                    Paper.keywords.ilike(json.dumps(kw, ensure_ascii=False)[1:-1]),
                    Paper.title.ilike(kw),
                    Paper.abstract.ilike(kw)
                ])
            conditions.append(or_(*keyword_conditions))
        results = session.execute(
            select(Paper.title)
            .join(PaperAuthorRelation, PaperAuthorRelation.paper_id == Paper.paper_id)
            .join(Author, Author.author_id == PaperAuthorRelation.author_id)
            .where(and_(*conditions))
            .distinct()
        ).scalars().all()
        titles = list(results)
        return dict(paper_count=len(titles), paper_titles=titles)


def _get_papers_with_entities_from_db(conference_short_name: str, year: int, entity_type: str):
    # todo: rewrite with NLP filter
    if not entity_type == "tool":
        raise NotImplementedError("Only supports 'tool' as parameter 'entity_type'.")
    with Database().get_session() as session:  # type: Session
        results = session.execute(
            select(Paper.paper_id, Paper.title, Paper.abstract)
            .where(
                and_(
                    Paper.conference_id.in_(_conference_of(conference_short_name, year)),
                    or_(Paper.abstract.ilike("%tool%"), Paper.abstract.ilike("%工具%"))
                )
            )
        ).all()
        return [dict(id=id_, title=title, abstract=abstract) for (id_, title, abstract) in results]


def _get_conference_sets_from_db() -> list[dict]:
    with Database().get_session() as session:  # type: Session
        query = select(Conference.short_name, Conference.year).distinct()
        return [dict(name=name, year=year) for (name, year) in session.execute(query).all()]


def _conference_of(conference_short_name: str | None, year: int) -> Select[tuple[str]]:
    return (
        select(Conference.conference_id)
        .where(
            and_(Conference.short_name.ilike(conference_short_name), Conference.year == year)
            if conference_short_name
            else Conference.year == year
        )
    )


def _institution_to_paper_id_view(conference_short_name: str | None, year: int) -> Select[tuple[str, str]]:
    """create a View with column `affiliation, paper_id`"""
    conference_view = _conference_of(conference_short_name, year)
    return (
        select(Author.affiliation.label("affiliation"),
               Paper.paper_id.label("paper_id"))
        .join(PaperAuthorRelation, PaperAuthorRelation.author_id == Author.author_id)
        .join(Paper, Paper.paper_id == PaperAuthorRelation.paper_id)
        .where(Paper.conference_id.in_(conference_view))
        .distinct()
    )


class _ConfigFile(BaseModel):
    database: DatabaseConfig


if __name__ == '__main__':
    if len(sys.argv) == 2:
        config_path = sys.argv[1]
    else:
        config_path = os.environ.get("DEEPINSIGHT_CONFIG_PATH")
        if not config_path:
            raise RuntimeError("需通过环境变量 'DEEPINSIGHT_CONFIG_PATH' 或命令行参数传入DeepInsight配置文件 config.yaml 路径。")
    with open(config_path, encoding="utf8") as f:
        Database(_ConfigFile.model_validate(yaml.safe_load(f)).database)
    mcp_app.run(transport=MCP_TRANSPORT)
