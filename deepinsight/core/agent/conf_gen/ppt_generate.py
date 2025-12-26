import asyncio
from enum import Enum
from datetime import datetime
import json
import logging
import os
from pathlib import PurePosixPath
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict, Union, get_args, get_origin, Callable, Type
from pydantic import BaseModel, Field

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.constants import END
from langgraph.graph import StateGraph

from deepinsight.core.tools.file_download import download_file_from_url
from deepinsight.core.types.graph_config import ResearchConfig
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.types.conference_constants import (
    ConferenceFileNames,
    ConferenceFolderNames,
)

DEFAULT_LIST_STYLE_DESC = "\n当输出多条内容时，采用markdown列表格式，先给出小标题，再给出内容，示例如下\n  * **你的小标题**: 具体内容\n* **小标题2**: 具体内容\n"
CONFERENCE_OVERVIEW_EXAMPLE = "以ICML为例: ICML以推动机器学习理论与应用的前沿研究为核心目标，涵盖监督学习、无监督学习、强化学习、生成式AI、多模态学习等基础领域，以及医疗、自动驾驶、气候变化等跨学科应用。作为机器学习领域的 旗舰会议，其论文质量和学术影响力被广泛认可，与NeurIPS、ICLR并称为全球三大机器学习顶会。"


# ========= 路径归一化工具 =========
def _resolve_chart_or_files_path(raw_path: Optional[str], rc: ResearchConfig) -> Optional[str]:
    """将 LLM/工具生成的相对路径转换为 PPT 可用的实际路径（基于路径分段匹配）。

    规则（分段匹配，不使用 substring）：
    - 绝对存在路径：直接返回。
    - 路径分段含 `rc.chart_image_dir`：拼为 `work_root/chart_image_dir/<tail>`。
    - 路径分段含 `conference_report_result` 与 `files`：拼为 `work_root/conference_report_result/<thread_id>/files/<tail>`。
    - 其他：原样返回。
    """
    if not raw_path:
        return raw_path

    try:
        # 已是绝对路径且存在
        if os.path.isabs(raw_path) and os.path.exists(raw_path):
            return raw_path

        work_root = rc.work_root or "./"
        chart_dir_cfg = (rc.chart_image_dir or "charts").lstrip("./")
        thread_id = rc.thread_id or "default_thread"

        # 用 POSIX 风格统一分段，避免不同分隔符与 ../../../ 的干扰
        raw_norm = raw_path.replace("\\", "/")
        posix = PurePosixPath(raw_norm)
        segments = list(posix.parts)
        filename = segments[-1] if segments else os.path.basename(raw_norm)

        # 处理图表目录（精确分段匹配），保留子目录结构
        if chart_dir_cfg and chart_dir_cfg in segments:
            idx = segments.index(chart_dir_cfg)
            tail_segments = segments[idx + 1:]
            tail_rel = str(PurePosixPath(*tail_segments)) if tail_segments else filename
            return os.path.join(work_root, chart_dir_cfg, tail_rel)

        # 处理 conference_report_result / files（精确分段匹配），保留 files 后的子目录
        if "conference_report_result" in segments:
            if "files" in segments:
                files_idx = segments.index("files")
                tail_after_files = segments[files_idx + 1:]
                tail_rel = str(PurePosixPath(*tail_after_files)) if tail_after_files else filename
                return os.path.join(work_root, "conference_report_result", thread_id, "files", tail_rel)
            else:
                return os.path.join(work_root, "conference_report_result", thread_id, filename)

    except Exception as e:
        logging.warning(f"[resolve_path] Unexpected error for '{raw_path}': {e}")

    # 默认返回原值，避免破坏
    return raw_path


def _normalize_image_paths_in_pages(pages: List[Dict[str, Any]], rc) -> None:
    """递归扫描 pages 中的所有 image 内容，规范化其 path 字段到实际可用路径。"""

    def _normalize(obj: Any):
        if isinstance(obj, dict):
            # 命中一个 image 内容
            if obj.get("type") == "image" and "path" in obj:
                obj["path"] = _resolve_chart_or_files_path(obj.get("path"), rc)
            # 递归子项
            for v in obj.values():
                _normalize(v)
        elif isinstance(obj, list):
            for item in obj:
                _normalize(item)
        # 其他类型忽略

    for page in pages:
        _normalize(page)


class PPTGraphNodeType(str, Enum):
    CHECK_EXISTING_PPT = "check_existing_ppt"
    LOAD_CONFERENCE_SECTIONS = "load_conference_sections"
    ASSEMBLE_PPT_JSON = "assemble_ppt_json"
    SAVE_PPT_JSON = "save_ppt_json"

    GENERATE_OVERVIEW_PAGE = "generate_overview_page"
    GENERATE_KEYNOTES_PAGE = "generate_keynotes_page"
    GENERATE_TOPIC_CONTENT_PAGE = "generate_topic_content_page"
    GENERATE_TOPIC_DETAILS_PAGE = "generate_topic_details_page"
    GENERATE_BEST_PAPERS_PAGE = "generate_best_papers_page"
    GENERATE_SUMMARY_PAGE = "generate_summary_page"

    GENERATE_TECH_THEME_PAGE = "generate_tech_theme_page"
    GENERATE_RESEARCH_HOTSPOT_COLLAB_01_PAGE = "generate_research_hotspot_collab_01_page"
    GENERATE_RESEARCH_HOTSPOT_COLLAB_02_PAGE = "generate_research_hotspot_collab_02_page"
    GENERATE_COUNTRY_TECH_FEATURE_PAGE = "generate_country_tech_feature_page"
    GENERATE_INSTITUTION_TECH_FEATURE_PAGE = "generate_institution_tech_feature_page"
    GENERATE_INSTITUTION_TECH_STRENGTH_PAGE = "generate_institution_tech_strength_page"
    GENERATE_INSTITUTION_COOPERATION_PAGE = "generate_institution_cooperation_page"
    GENERATE_HIGH_POTENTIAL_TECH_TRANSFER_PAGE = "generate_high_potential_tech_transfer_page"

    def __str__(self):
        return self.value


class PPTState(TypedDict):
    ppt_json_file_path: Optional[str]
    ppt_generate_file_name: Optional[str]
    ppt_json: Optional[List[Dict[str, Any]]]
    sections: Optional[Dict[str, Any]]
    overview_json: Optional[Any]
    keynote_json: Optional[Any]
    topic_content_json: Optional[Any]
    topic_details_json: Optional[List[Any]]
    best_papers_json: Optional[List[Any]]
    summary_json: Optional[Any]

    tech_theme_page_json: Optional[Any]
    research_hotspot_collab_01_page_json: Optional[Any]
    research_hotspot_collab_02_page_json: Optional[Any]
    country_tech_feature_page_json: Optional[Any]
    institution_tech_feature_page_json: Optional[Any]
    institution_tech_strength_page_json: Optional[Any]
    institution_cooperation_page_json: Optional[Any]
    high_potential_tech_transfer_page_json: Optional[Any]


# ========== 通用结构 ==========
class ImageContent(BaseModel):
    """图片内容描述"""
    type: Optional[str] = Field("image", description="类型为 image")
    path: Optional[str] = Field(None,
                                description="图片路径，必须为本地文件路径，如果是远端地址请先使用文件下载工具下载到本地文件系统，下载时候文件名按照图片描述起名，避免用JSON里面对应的key，因为可能会重复")


class TableContent(BaseModel):
    """表格内容描述"""
    type: Optional[str] = Field("table", description="类型为 table")
    path: Optional[str] = Field(None, description="表格文件路径，和content二选一即可")
    content: Optional[str] = Field(None,
                                   description="表格csv实际内容，和path二选一即可，请注意，csv某个单元格如果有逗号等符合，整个单元格需要用引号")


class BasePage(BaseModel):
    """所有 PPT 页面基类"""
    type: str


# ========== 各类型页面定义 ==========
class TechThemePageContent(BaseModel):
    tech_field_png: Optional[ImageContent] = Field(None, description="技术主题分析图")
    key_tech_intro: Optional[str] = Field(None, description="技术主题分析介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1.主题概览、2.趋势分析、3.主题展望，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行；")
    key_tech_summary: Optional[str] = Field(None, description="一段话，技术主题总结与洞察，长度100-200，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class TechThemePage(BasePage):
    type: str = "tech_theme_page"
    content: TechThemePageContent


# =============== C. 研究热点与跨区域技术合作（01） ===============
class ResearchHotspotCollab01PageContent(BaseModel):
    keyword_cloud_png: Optional[ImageContent] = Field(None, description="关键词云图")
    keyword_intro: Optional[str] = Field(None, description="关键词与研究热点介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1. 关键词分布概述、2. 关键词趋势分析、3. 技术领域融合分布概述、4. 技术领域融合分析，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行")
    keyword_couple_analysis_png: Optional[ImageContent] = Field(None,
                                                               description="关键词耦合分析图")
    keyword_summary: Optional[str] = Field(None, description="一段话，仅对关键词相关内容进行总结，不需要涉及和主题相关内容，长度100字以内，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class ResearchHotspotCollab01Page(BasePage):
    type: str = "research_hotspot_collab_01_page"
    content: ResearchHotspotCollab01PageContent


# =============== D. 研究热点与跨区域技术合作（02） ===============
class ResearchHotspotCollab02PageContent(BaseModel):
    keyword_topic_csv: Optional[TableContent] = Field(None, description="关键词主题分析表格")
    keyword_topic_intro: Optional[str] = Field(None, description="关键词主题分布介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息,标题加粗：1.概述、2.技术趋势，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行")
    keyword_topic_summary: Optional[str] = Field(None, description="一段话，关键词主题总结与趋势洞察，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，长度100字左右；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class ResearchHotspotCollab02Page(BasePage):
    type: str = "research_hotspot_collab_02_page"
    content: ResearchHotspotCollab02PageContent


# =============== E. 国家/地区技术特征分析 ===============
class CountryTechFeaturePageContent(BaseModel):
    country_tech_top_png: Optional[ImageContent] = Field(None,
                                                        description="国家/地区技术热度图")
    country_tech_strength_csv: Optional[TableContent] = Field(None,
                                                             description="国家/地区技术强度表格，通常国家地区众多，因此精简原始数据，每个国家或地区选取占比最高的两条记录，如美国只可以出现两行，中国只可出现两行，例如 国家/地区,技术优势领域,占比\n美国,大数据与机器学习系统,34.2%\n美国,操作系统,14.4%\n中国,大数据与机器学习系统,45.5%\n中国,文件与存储系统,16.2% ...")
    country_tech_intro: Optional[str] = Field(None, description="国家/地区技术特征介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息，标题加粗：1.概述、2.中国技术特征，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行；内容要求禁止空泛的总结句，直接、具体地切入主题")
    country_tech_summary: Optional[str] = Field(None, description="一段话，国家/地区技术特征总结，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，长度100字左右；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class CountryTechFeaturePage(BasePage):
    type: str = "country_tech_feature_page"
    content: CountryTechFeaturePageContent


# =============== F. 机构技术特征分析 ===============
class InstitutionTechFeaturePageContent(BaseModel):
    top_institution_png: Optional[ImageContent] = Field(None,
                                                       description="领先机构分布图")
    institution_tech_feat_intro: Optional[str] = Field(None, description="机构技术特征介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1.概述、2.机构研究重点、3.产学研分析、4.中国机构概述，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行;内容要求禁止空泛的总结句，直接、具体地切入主题;涉及到 企业 高校 字样用红色标记")
    compony_school_analysis_png: Optional[ImageContent] = Field(None,
                                                               description="企业与高校分布分析图")
    institution_tech_feat_summary: Optional[str] = Field(None, description="一段话，总结机构技术特征，长度100字左右，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，；关键内容使用黄色标记，但不要整个字段内容都是黄色的")


class InstitutionTechFeaturePage(BasePage):
    type: str = "institution_tech_feature_page"
    content: InstitutionTechFeaturePageContent


# =============== G. 机构技术优势分析 ===============
class InstitutionTechStrengthPageContent(BaseModel):
    university_tech_strength_csv: Optional[TableContent] = Field(None,
                                                                 description="高校技术强度表格，通常高校众多，因此精简原始数据，每个高校选取占比最高的一条记录即可，总行数最多不要超过8条")
    compony_tech_strength_csv: Optional[TableContent] = Field(None,
                                                             description="企业技术强度表格，通常企业众多，因此精简原始数据，每个企业选取占比最高的两条记录即可")
    institution_tech_strength_intro: Optional[str] = Field(None, description="机构技术优势介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1.高校技术优势分析及趋势、2.企业技术优势分析及趋势总结、3.启示，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行；内容要求禁止空泛的总结句，直接、具体地切入主题")
    institution_tech_strength_summary: Optional[str] = Field(None, description="一段话，机构技术优势总结，长度100-200，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class InstitutionTechStrengthPage(BasePage):
    type: str = "institution_tech_strength_page"
    content: InstitutionTechStrengthPageContent


# =============== H. 跨机构合作网络分析 ===============
class InstitutionCooperationPageContent(BaseModel):
    institution_cooperation_png: Optional[ImageContent] = Field(None,
                                                               description="跨机构合作网络图")
    institution_cooperation_intro: Optional[str] = Field(None, description="跨机构合作网络介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1.合作网络概述、2.TOP3合作网络、3.企业合作网络、4.华为合作网络，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行；内容要求禁止空泛的总结句，直接、具体地切入主题")
    institution_cooperation_summary: Optional[str] = Field(None, description="一段话，跨机构合作网络总结与洞察，长度100-200，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class InstitutionCooperationPage(BasePage):
    type: str = "institution_cooperation_page"
    content: InstitutionCooperationPageContent


# =============== I. 高潜技术转化分析 ===============
class HighPotentialTechTransferPageContent(BaseModel):
    high_potential_csv: Optional[TableContent] = Field(None,
                                                      description="高潜技术转化相关数据表格")
    high_potential_intro: Optional[str] = Field(None, description="高潜技术转化分析介绍，长度100-200，请从原文中的以下内容获取对应信息，并且精简对应内容（事实描述用一句话略写）并保留全部主要信息：1.概述、2.Top3高潜技术、3.业务启示，对于每部分内容用段落形式，标题需带有序号且加粗，内容中关键信息用红色标记，换行时不要空行；内容要求禁止空泛的总结句，直接、具体地切入主题")
    high_potential_summary: Optional[str] = Field(None, description="一段话，高潜技术转化总结与趋势洞察，长度100-200，如果原文中有一句话总结 内容，则直接借鉴原文，但内容要求禁止空泛的总结句，直接、具体地切入主题，如第一句不以“基于”开头，；关键内容使用黄色标记，但不要整个字段内容都是黄色的")

class HighPotentialTechTransferPage(BasePage):
    type: str = "high_potential_tech_transfer_page"
    content: HighPotentialTechTransferPageContent


class CoverPageContent(BaseModel):
    conference_name: Optional[str] = Field(None, description="会议名称")
    date: Optional[str] = Field(None, description="会议日期，格式如 2023年10月15-18日")


class CoverPage(BasePage):
    type: str = "cover_page"
    content: CoverPageContent


class ContentPage(BasePage):
    type: str = "content_page"
    skip_fill: bool = True


# --- Conference Overview ---
class ConfOverviewPageContent(BaseModel):
    conf_name: Optional[str] = Field(None, description="会议名称，长度10以内")
    conf_info: Optional[str] = Field(None, description="会议基本信息概述，长度200-300," + CONFERENCE_OVERVIEW_EXAMPLE)
    organizer_level: Optional[str] = Field(None, description="会议级别，长度128以内")
    conf_topics: Optional[str] = Field(None, description="会议主题介绍，长度128以内")
    conf_loc: Optional[str] = Field(None, description="会议地点，长度50以内")
    conf_date: Optional[str] = Field(None, description="会议时间，长度50以内")
    conf_sponsor: Optional[str] = Field(None, description="会议主办方，长度80以内")
    conf_chair: Optional[str] = Field(None, description="会议主席，长度80以内")
    conf_committee: Optional[str] = Field(None, description="会议委员会，长度80以内")
    conf_institution: Optional[str] = Field(None, description="会议主要机构，长度80以内")
    submit_papers: Optional[str] = Field(None, description="会议投稿情况概述，长度80以内")
    total_trend: Optional[str] = Field(
        None, 
        description="会议论文总体趋势分析描述，使用markdown列表写法列出多条，长度300-400" + DEFAULT_LIST_STYLE_DESC
    )


class ConfOverviewPage(BasePage):
    type: str = "conf_overview_page"
    content: ConfOverviewPageContent


# --- Research Fields ---
class ResearchFieldsPageContent(BaseModel):
    research_trend: Optional[str] = Field(None,
                                          description="论文主题领域趋势分析描述，使用markdown列表写法列出多条，长度400-600" + DEFAULT_LIST_STYLE_DESC)
    research_fields_png: Optional[ImageContent] = Field(None,
                                                        description="论文Top主题领域趋势分析图片")

class ResearchFieldsPage(BasePage):
    type: str = "research_fields_page"
    content: ResearchFieldsPageContent


# --- Country Analysis ---
class CountryAnalysisPageContent(BaseModel):
    country_trend: Optional[str] = Field(None,
                                         description="国家/地区趋势分析描述，使用markdown列表写法列出多条，长度400-600" + DEFAULT_LIST_STYLE_DESC)
    country_png: Optional[ImageContent] = Field(None, description="国家/地区趋势分析图片")


class CountryAnalysisPage(BasePage):
    type: str = "country_analysis_page"
    content: CountryAnalysisPageContent


# --- Institution Analysis ---
class InstitutionAnalysisPageContent(BaseModel):
    institution_trend: Optional[str] = Field(None,
                                             description="机构趋势分析描述，使用markdown列表写法列出多条，长度400-600" + DEFAULT_LIST_STYLE_DESC)
    institution_png: Optional[ImageContent] = Field(None, description="机构趋势分析图片")


class InstitutionAnalysisPage(BasePage):
    type: str = "institution_analysis_page"
    content: InstitutionAnalysisPageContent


# --- First Author ---
class FirstAuthorPageContent(BaseModel):
    first_author_statistic_csv: Optional[TableContent] = Field(None,
                                                               description="第一作者统计表格内容")


class FirstAuthorPage(BasePage):
    type: str = "first_author_page"
    content: FirstAuthorPageContent


# --- Coauthor ---
class CoauthorPageContent(BaseModel):
    coauthor_statistic_csv: Optional[TableContent] = Field(None,
                                                           description="合作作者统计表格内容")


class CoauthorPage(BasePage):
    type: str = "coauthor_page"
    content: CoauthorPageContent


# --- Keynote Page ---
class KeynotePageContent(BaseModel):
    keynote_title: Optional[str] = Field(
        None,
        description="主旨演讲标题，需准确概括演讲核心主题，体现前瞻性和专业性"
    )

    speaker: Optional[str] = Field(
        None,
        description="""演讲嘉宾信息（长度50-100字）。
核心要点：突出嘉宾与主题的关联性及权威性；
撰写特点：简洁凝练，聚焦“身份标签+核心成就”，优先选择与主题直接相关的经历；
示例方向：XX大学计算机科学系教授，ACM Fellow，长期深耕人工智能生成式模型领域，主导开发了XX模型。"""
    )

    keynote_abstract: Optional[str] = Field(
        None,
        description="""主要内容和思想（长度100-200字）。
核心要点：梳理逻辑框架与核心观点，提炼最具价值的思想主张；
撰写特点：结构化呈现（背景铺垫-核心观点-论据支撑），突出创新性和前瞻性，标注打破传统认知的关键思想；
示例方向：首先分析XX技术的“效率瓶颈”与“伦理争议”，接着提出“XX融合架构”解决方案，最后强调“技术向善”思想。"""
    )

    keynote_background: Optional[str] = Field(
        None,
        description="""主旨演讲why（为何重要/为何关注，长度100-200字，优先从源数据对应的why获取）。
核心要点：阐明时代背景、行业痛点或战略意义，回答“为何值得关注”；
撰写特点：结合宏观趋势与实际需求，从行业价值、技术突破等角度切入，用数据/案例增强说服力；
示例方向：全球XX市场年复合增长率达XX%，但面临“落地成本高”痛点，该演讲方案可降低成本XX%，为行业规避风险提供参考。"""
    )

    keynote_objective: Optional[str] = Field(
        None,
        description="""主旨演讲what（核心是什么/解决什么问题，长度100-200字，优先从源数据对应的what获取）。
核心要点：明确聚焦的核心问题及提出的核心概念、方案；
撰写特点：精准聚焦，用“问题-答案”逻辑呈现，可对比传统做法与新方式凸显差异；
示例方向：核心问题是“如何在保证XX模型精度的前提下降低算力依赖”，提出“轻量化蒸馏+动态剪枝”策略，实现精度损失<XX%且算力降XX倍。"""
    )

    keynote_method: Optional[str] = Field(
        None,
        description="""主旨演讲how（如何实现/实施路径，长度100-200字）。
核心要点：概述实现核心目标的方法、步骤或路径，体现可行性；
撰写特点：逻辑清晰，分点不冗长，侧重方法论或框架性指导，可提案例关键节点；
示例方向：实施路径分三步：1.用XX算法预处理数据筛选核心特征；2.基于XX框架构建轻量化模型并迁移知识；3.引入动态监控调整参数。"""
    )

    keynote_inspiration: Optional[str] = Field(
        None,
        description="""主旨演讲对业务启示（长度100-200字）。
核心要点：连接演讲内容与自身业务，提炼可落地、可借鉴的启示；
撰写特点：针对性强，结合具体业务领域（研发/运营/布局等），提出具体行动方向而非抽象理念；
示例方向：对AI产品研发：可引入“动态剪枝”提升移动端速度，布局伦理合规模块；对市场：推出轻量化方案填补下沉市场空白。"""
    )

    keynote_summary: Optional[str] = Field(
        None,
        description="""主旨演讲总结（长度128-240字）。
核心要点：高度概括演讲价值，强化关键信息，形成认知闭环；
撰写特点：简洁有力（3-5句话），涵盖核心价值+关键启示+未来展望，语言具有升华性；
示例方向：本次演讲围绕XX技术“效率与伦理”双核心，提出创新可行方案，其思想与路径为业务提供指引，未来可关注该领域技术演进。"""
    )
    keynote_picture: Optional[ImageContent] = Field(
        None,
        description="演讲嘉宾照片，如果源数据里面有，则必须填写"
    )


class KeynotePage(BasePage):
    type: str = "keynote_page"
    content: KeynotePageContent


class KeynotePageContentList(BaseModel):
    items: Optional[List[KeynotePageContent]]


# --- Topic Content Page ---
class TopicContentPageContent(BaseModel):
    topic_content_csv: Optional[TableContent] = Field(None,
                                                      description=f"""
会议技术专题，表头包含专题方向和相关论文及摘要，内容见技术方向总览相关部分，长度600-800，
csv的每个单元格都必须用双引号包裹（包括数字）,单元格里面不同论文及其摘要需要使用换行符分开，
单个专题对应论文及其摘要示例：

- 论文1：论文1摘要
- 论文2：论文2摘要
""")


class TopicContentPage(BasePage):
    type: str = "topic_content_page"
    content: TopicContentPageContent


# --- Topic Detail Page ---
class TopicDetailPageContent(BaseModel):
    topic_title: Optional[str] = Field(
        None,
        description="""会议主题标题。
核心目标：精准概括顶会Topic的核心内容，体现领域属性与研究焦点；
撰写要点：包含领域范畴（如AI、CV、NLP）和核心对象（如少样本语义分割、大模型效率优化）；
示例方向：“NeurIPS近3年少样本语义分割研究进展与趋势”"""
    )

    topic_overview: Optional[str] = Field(
        None,
        description="""会议主题概述（长度140-280字）。
核心目标：用简洁语言让观众快速了解Topic的“是什么”，明确讨论范围和核心对象；
撰写要点：① 定义Topic在所属领域的具体范畴；② 点出试图解决的关键问题；③ 说明技术边界（如时间范围、场景限制）；④ 一句话概括价值定位；
内容特点：简洁性（避免冗长术语）、准确性（定义无歧义）、引导性（为后续内容铺垫框架）；
示例方向：“本Topic聚焦顶会NeurIPS近3年的‘少样本语义分割’研究，旨在解决传统分割模型依赖大量标注数据的痛点，核心是通过迁移学习与元学习方法提升小数据场景下的分割精度，是计算机视觉在低资源任务中的关键方向之一。”"""
    )

    topic_reason: Optional[str] = Field(
        None,
        description="""会议主题选择原因（长度500-800字，不要包含引导性开头的句子）。
核心目标：从学术、产业、社会等多维度说明Topic的研究意义，回答“为什么值得关注”；
撰写要点：① 学术价值：填补领域空白、推动理论发展；② 产业需求：对应实际场景痛点、潜在应用价值；③ 行业趋势：结合顶会热点、政策或技术演进方向；④ 对比反衬：通过传统方法局限性突出必要性；
内容特点：说服力（用数据或案例支撑）、关联性（紧扣领域痛点）、前瞻性（点明长期价值）；
示例方向：“少样本语义分割的重要性体现在三方面：学术上，突破了‘数据饥渴’的传统分割范式，推动元学习与视觉表征融合的理论创新；产业上，医疗影像标注成本高达每张图数百元，该技术可将标注需求降低90%，显著提升诊断效率；趋势上，随着边缘设备算力受限场景增多，小数据训练的模型更符合实际部署需求，是CV领域从‘实验室’走向‘产业化’的关键跳板。”"""
    )

    topic_method_innovation: Optional[str] = Field(
        None,
        description=f"""会议主题主要技术路径与创新点（长度500-1000，不要包含引导性开头的句子，使用markdown列表写法列出多条，每条用-开头，示例：* **- 条目1**：条目1描述 \n* **- 条目2**：条目2描述）。
核心目标：系统梳理主流技术方向，提炼顶会成果的关键创新，体现“怎么做”的核心逻辑；
撰写要点：① 按方法论或核心模块分类技术路径；② 每条路径说明“核心思想+顶会代表工作+技术细节”；③ 从方法、理论、性能三层面总结创新点；④ 对比不同路径的优缺点；
内容特点：结构性（分类清晰）、专业性（突出顶会级技术细节）、创新性（聚焦差异化贡献）；
示例方向： * ** - 元学习路径 **：核心是“任务级训练”，代表工作如ICCV
    2023
    的MetaSeg，通过构建大量小样本分割任务进行元训练，创新点在于引入“动态任务适配器”解决不同任务间的分布偏移问题，在PASCAL - 5
    i数据集上5 - shot
    mIoU达62
    .3 %
    * ** - 迁移学习路径 **：基于预训练ViT模型，代表工作如CVPR
    2024
    的TransSeg，创新点是“跨模态知识蒸馏”，将ImageNet的分类知识迁移到分割任务，训练效率提升30 %
                                                                           * ** - Prompt路径 **：通过视觉Prompt引导模型关注前景目标，代表工作如ECCV
    2024
    的PromptSeg，创新点在于“自适应Prompt生成”，无需人工设计模板，小样本场景下鲁棒性更优
    """)

    topic_inspiration: Optional[str] = Field(
        None,
        description="""
    会议主题对业务启示（长度300 - 600
    字，不要包含引导性开头的句子）。
    核心目标：提炼可复用的技术方法或思维模式，回答“对其他研究或工作有何启发”；
    撰写要点：① 技术复用：具体可迁移的模块（如注意力机制、损失函数）；② 思想启发：研究范式或思维模式（如任务分解、数据分布优化）；③ 跨领域延伸：结合其他领域痛点的应用场景；④ 落地建议：对产业界的实践启示；
    内容特点：迁移性（明确可复用场景）、启发性（提炼方法论）、实用性（给出具体建议）；
    示例方向：“技术上，MetaSeg的‘动态任务适配器’可直接复用至少样本目标检测，解决检测任务中目标尺度变化的问题；思想上，‘通过构建多样化任务集提升泛化性’的思路，可推广到低资源机器翻译；产业落地方面，中小企业可借鉴‘基于预训练模型做轻量化微调’的路径，在医疗影像小数据场景中快速实现技术落地。”"""
    )

    topic_summary: Optional[str] = Field(
        None,
        description="""
    会议主题总结（长度200 - 400
    字）。
    核心目标：回顾关键内容，升华主题价值，给出未来展望，强化核心认知；
    撰写要点：① 核心回顾：概括Topic定义、核心价值及主流技术路径；② 关键结论：提炼研究共识；③ 现存挑战：指出未解决的问题；④ 未来展望：结合顶会趋势给出方向；⑤ 收尾升华：强调长期意义；
    内容特点：凝练性（突出重点）、客观性（正视挑战）、前瞻性（引导未来关注）；
    示例方向：“综上，少样本语义分割是解决CV低资源任务的关键Topic，通过元学习、迁移学习等路径实现了数据高效分割，核心价值在于平衡模型性能与标注成本。当前研究已达成‘多方法融合提升泛化性’的共识，但极端少样本场景下的鲁棒性仍是挑战。未来可结合大模型上下文学习能力突破，推动低资源智能技术的实际落地与普惠。”"""
    )


class TopicDetailPage(BasePage):
    type: str = "topic_detail_page"
    content: TopicDetailPageContent


class TopicDetailPageContentList(BaseModel):
    items: Optional[List[TopicDetailPageContent]]


# --- Valuable Paper Page ---
class ValuablePaperPageContent(BaseModel):
    tech_topic: Optional[str] = Field(
        None,
        description="主题，即论文所属核心研究领域或方向。\n"
                    "写作要点：\n"
                    "- 避免冗长\n"
                    "- 体现学科分支或技术方向\n"
                    "示例：机密计算、内核安全、虚拟化技术"
    )
    paper_headline: Optional[str] = Field(
        None,
        description="论文总结标题，需提炼核心创新价值。\n"
                    "写作要点：\n"
                    "- 格式为「解决XXX问题/的XXX新架构/技术突破/技术创新」\n"
                    "- 突出技术独创性或应用场景\n"
                    "示例：\n"
                    "- 面向机密虚机的安全半虚拟化VMM(paravisor)\n"
                    "- 基于Rust的特权分离高安全高性能内核架构"
    )
    paper_title: Optional[str] = Field(
        None,
        description="论文标题，即论文正式发表的原始标题。\n"
                    "写作要点：\n"
                    "- 完整保留标题中的专业术语和符号\n"
                    "- 如实呈现大小写和标点符号\n"
                    "- 保留原文中题目前面的【】包含的描述\n"
                    "示例：【获奖论文】ParaVisor: A Secure Semi-Virtualization VMM for Confidential VMs"
    )
    paper_background: Optional[str] = Field(
        None,
        description="论文背景介绍（140-280字）。\n"
                    "写作要点：\n"
                    "- 包含一句话总结的关键挑战或新发现\n"
                    "- 核心描述需用加粗突出\n"
                    "- 说明研究的现实必要性\n"
                    "示例：随着云计算普及，机密数据在虚拟化环境中的保护成为刚需。当前VMM存在「过度特权导致的安全边界模糊」问题，攻击者可通过漏洞劫持VMM窃取虚机数据。本文针对这一痛点，探索轻量型安全虚拟化的新路径。"
    )
    paper_key_tech: Optional[str] = Field(
        None,
        description="论文关键技术（300-600字）。\n"
                    "写作要点：\n"
                    "1. 总分结构：先一句话总结技术价值，再列表展开\n"
                    "2. 一句话总结：「通过XX技术实现XX效果」\n"
                    "3. 列表项需包含「技术点+效果」，关键信息加粗\n"
                    "示例：通过「特权分离+最小权限原则」设计，实现机密虚机安全与性能的平衡。\n1. **动态特权降级机制**：根据虚机运行状态实时调整VMM权限，攻击面减少62%；\n2. **硬件辅助内存加密**：结合Intel SGX扩展，虚机内存访问延迟仅增加8ms；\n3. **模块化验证框架**：自动化验证核心逻辑，漏洞检出率提升至97%。"
    )
    paper_result: Optional[str] = Field(
        None,
        description="论文实验结果（250-500字）。\n"
                    "写作要点：\n"
                    "1. 总分结构：先一句话概括场景与效果，再列表量化结果\n"
                    "2. 列表项需包含「维度+量化数据+对比提升（如有）」\n"
                    "示例：在金融云虚机场景下，性能与安全性全面优于现有方案。\n1. 启动速度：平均2.3秒，较KVM快41%；\n2. 安全强度：通过Common Criteria EAL5+认证，较Xen多抵御17类攻击；\n3. 资源占用：内存开销85MB，仅为传统VMM的1/5。"
    )
    paper_summary: Optional[str] = Field(
        None,
        description="论文总结（100-200字）。\n"
                    "写作要点：\n"
                    "- 基于技术创新分析未来方向\n"
                    "- 可提及技术演进趋势或突破思路\n"
                    "示例：本文提出的半虚拟化安全架构，为机密计算提供了「轻量可信基」的新范式。未来可进一步探索Rust全栈虚拟化实现，结合形式化验证构建零信任VMM，有望成为云原生安全的核心技术方向。"
    )
    key_tech_png: Optional[ImageContent] = Field(None, description="关键技术图片1")
    exp_result_png: Optional[ImageContent] = Field(None, description="实验结果图片1")


class ValuablePaperPage(BasePage):
    type: str = "valuable_paper_page"
    content: ValuablePaperPageContent


# --- Conference Summary Page ---
class ConfSummaryPageContent(BaseModel):
    key_trends: Optional[str] = Field(None, description="关键趋势总结，使用markdown列表写法列出多条，长度300-500,你需要按照原文的格式换行，内容一般是成段出现的，不要一句话就换行")
    suggestions: Optional[str] = Field(None, description="建议，使用markdown列表写法列出多条，长度300-500，你需要按照原文的格式换行，内容一般是成段出现的，不要一句话就换行")


class ConfSummaryPage(BasePage):
    type: str = "conf_summary_page"
    content: ConfSummaryPageContent


async def check_existing_ppt(state: PPTState, config: RunnableConfig):
    rc = parse_research_config(config)
    current_thread_work_root = os.path.join(rc.work_root, "conference_report_result", rc.thread_id)
    ppt_json_file_name = os.path.join(current_thread_work_root, "ppt_content.json")
    ppt_file_name = os.path.join(current_thread_work_root, "result.pptx")
    ppt_json = None
    if os.path.exists(ppt_json_file_name):
        with open(ppt_json_file_name, "r", encoding="utf-8") as f:
            ppt_json = json.load(f)

        cover_page = next((item for item in ppt_json if item.get("type") == "cover_page"), None)

        if cover_page and "content" in cover_page:
            content = cover_page["content"]
            conference_name = content.get("conference_name", "")
            now = datetime.now()
            time_for_filename = now.strftime("%Y%m%d%H%M%S")
            formatted_date = now.strftime("%Y年%m月%d日 %H点%M分")
            content["DATE"] = formatted_date

            ppt_file_name = os.path.join(
                current_thread_work_root,
                f"{conference_name} 洞察报告-{time_for_filename}.pptx"
            )

        with open(ppt_json_file_name, "w", encoding="utf-8") as f:
            json.dump(ppt_json, f, ensure_ascii=False, indent=2)

    return dict(
        ppt_json=ppt_json,
        ppt_generate_file_name=ppt_file_name,
        ppt_json_file_path=ppt_json_file_name if ppt_json is not None else None,
    )


async def load_conference_sections(state: PPTState, config: RunnableConfig):
    rc = parse_research_config(config)
    current_thread_work_root = os.path.join(rc.work_root, "conference_report_result", rc.thread_id)
    sections: Dict[str, str] = {}
    for fname in [
        ConferenceFileNames.OVERVIEW_MD,
        ConferenceFileNames.SUBMISSION_MD,
        ConferenceFileNames.KEYNOTES_MD,
        ConferenceFileNames.TOPIC_MD,
        ConferenceFileNames.SUMMARY_MD,
    ]:
        path = os.path.join(current_thread_work_root, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                sections[fname] = f.read()
        else:
            sections[fname] = ""

    bp_folder = os.path.join(current_thread_work_root, ConferenceFolderNames.BEST_PAPERS)
    best_papers_texts = []
    if os.path.isdir(bp_folder):
        for fn in os.listdir(bp_folder):
            if fn.endswith(".md"):
                with open(os.path.join(bp_folder, fn), "r", encoding="utf-8") as f:
                    best_papers_texts.append(f.read())
    sections[ConferenceFolderNames.BEST_PAPERS] = best_papers_texts
    statistic_folder = os.path.join(current_thread_work_root, ConferenceFolderNames.VALUE_MINING)
    if os.path.isdir(statistic_folder):
        for fn in os.listdir(statistic_folder):
            if fn.endswith(".md"):
                with open(os.path.join(statistic_folder, fn), "r", encoding="utf-8") as f:
                    sections[fn] = f.read()
    return dict(
        sections=sections
    )


def generate_json_template(model_cls: type) -> str:
    """
    将 Pydantic 模型类转为包含类型、描述、示例值的 JSON 模板。
    ✅ 支持嵌套 BaseModel
    ✅ 支持 alias
    ✅ 支持 List[BaseModel] / Dict[str, BaseModel] 顶层输入
    """

    def type_name(field_type):
        origin = get_origin(field_type)
        if origin is Union:
            args = [t for t in get_args(field_type) if t is not type(None)]
            return f"Optional[{type_name(args[0])}]" if args else "Any"
        if isinstance(field_type, type):
            if issubclass(field_type, BaseModel):
                return field_type.__name__
            return field_type.__name__
        if origin in (list, List):
            args = get_args(field_type)
            return f"List[{type_name(args[0])}]" if args else "List"
        if origin in (dict, Dict):
            args = get_args(field_type)
            return f"Dict[{', '.join(type_name(a) for a in args)}]" if args else "Dict"
        return str(field_type)

    def default_value_for_type(field_type):
        origin = get_origin(field_type)
        if origin is Union:
            args = [t for t in get_args(field_type) if t is not type(None)]
            return default_value_for_type(args[0]) if args else None
        if isinstance(field_type, type) and issubclass(field_type, BaseModel):
            return build_template(field_type)
        if origin in (list, List):
            args = get_args(field_type)
            if args:
                inner_type = args[0]
                if isinstance(inner_type, type) and issubclass(inner_type, BaseModel):
                    return [build_template(inner_type)]
                else:
                    return [default_value_for_type(inner_type)]
            return []
        if origin in (dict, Dict):
            args = get_args(field_type)
            if len(args) == 2:
                key_type, val_type = args
                if isinstance(val_type, type) and issubclass(val_type, BaseModel):
                    return {"key": build_template(val_type)}
                else:
                    return {"key": default_value_for_type(val_type)}
            return {}
        if field_type == str:
            return ""
        if field_type in [int, float]:
            return 0
        if field_type == bool:
            return False
        return None

    def build_template(model_cls):
        """递归构建包含类型和描述的模板"""
        template = {}
        for field_name, field_info in model_cls.model_fields.items():
            key_name = field_info.alias or field_name
            field_type = field_info.annotation
            field_description = field_info.description or ""
            field_value = {
                "type": type_name(field_type),
                "description": field_description,
                "example": default_value_for_type(field_type)
            }
            template[key_name] = field_value
        return template

    # ✅ 顶层类型修正逻辑
    origin = get_origin(model_cls)
    if origin in (list, List):
        args = get_args(model_cls)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            template = [build_template(args[0])]
        else:
            template = [default_value_for_type(args[0]) if args else None]
    elif origin in (dict, Dict):
        args = get_args(model_cls)
        if len(args) == 2 and isinstance(args[1], type) and issubclass(args[1], BaseModel):
            template = {"key": build_template(args[1])}
        else:
            template = {"key": default_value_for_type(args[1]) if len(args) == 2 else None}
    elif isinstance(model_cls, type) and issubclass(model_cls, BaseModel):
        template = build_template(model_cls)
    else:
        raise TypeError(f"Unsupported type for generate_json_template: {model_cls}")

    return json.dumps(template, indent=2, ensure_ascii=False)


def make_generate_page(
    page_content_cls: Type[BaseModel],
    page_model_cls: Type[BaseModel],
    md_filename: str,
    return_key: str,
    prompt_name: str = "default",
    tools: list = None,
) -> Callable[[object, object], object]:
    """
    生成一个异步 page 生成函数的闭包。
    参数：
      - page_content_cls: Pydantic 模型类，用于生成 response_format（如 TechThemePageContent）
      - page_model_cls: 返回的 page wrapper 类（如 TechThemePage）
      - md_filename: 在 state['sections'] 中查找的 md 文件名（字符串）
      - return_key: 返回字典中的 key 名称（例如 'tech_theme_page_json'）
      - prompt_name: 使用的 prompt 模板名称（默认 'default'）
      - tools: 可选的工具列表（默认 [download_file_from_url] 在调用处传入或 None）

    返回：
      - 一个 async 函数 (state: PPTState, config: RunnableConfig) -> dict | None
    """
    if tools is None:
        tools = [download_file_from_url]

    async def _generate(state: "PPTState", config: "RunnableConfig"):
        md_content = state["sections"].get(md_filename, "")
        if not md_content:
            logging.warning(f"Source markdown {md_filename!r} is empty for {return_key}")
            return

        rc = parse_research_config(config)
        prompt = rc.prompt_manager.get_prompt(
            name=prompt_name, 
            group=rc.prompt_group
        ).format(
            response_format=generate_json_template(page_content_cls),
        )

        llm = rc.get_model()
        agent = create_agent(
            model=llm,
            system_prompt=prompt,
            tools=tools,
            response_format=page_content_cls
        )

        try:
            response = await agent.with_retry().ainvoke(
                input=dict(messages=[HumanMessage(content=md_content)])
            )
        except Exception as e:
            logging.exception(f"Error invoking agent for {return_key}: {e}")
            return

        structured_response = response.get("structured_response")
        if not structured_response:
            logging.warning(f"LLM generated empty structured_response for {return_key}")
            return

        # page_model_cls 期望形如 TechThemePage(content=structured_response)
        try:
            page_obj = page_model_cls(content=structured_response)
        except Exception as e:
            # 兼容一些 Page 类可能需要额外字段的情况
            logging.exception(f"Failed to construct page model for {return_key}: {e}")
            return

        return {return_key: page_obj}

    return _generate


async def generate_overview_page(state: PPTState, config: RunnableConfig):
    md_content = state["sections"].get(ConferenceFileNames.OVERVIEW_MD, "")
    if not md_content:
        logging.warning(f"Overview page is empty")
        return
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("default", rc.prompt_group).format(
        response_format=generate_json_template(ConfOverviewPageContent),
    )
    llm = rc.default_model
    agent = create_agent(
        model=llm,
        system_prompt=prompt,
        tools=[download_file_from_url],
        response_format=ToolStrategy(ConfOverviewPageContent)
    )
    response = await agent.with_retry().ainvoke(
        input=dict(
            messages=[HumanMessage(content=state["sections"].get(ConferenceFileNames.OVERVIEW_MD, ""))]
        )
    )
    structured_response = response.get("structured_response")
    if not structured_response:
        logging.warning(f"LLM generate overriew response is empty")
        return
    return dict(
        overview_json=ConfOverviewPage(
            content=structured_response
        )
    )


async def generate_keynotes_page(state: PPTState, config: RunnableConfig):
    md_content = state["sections"].get(ConferenceFileNames.KEYNOTES_MD, "")
    if not md_content:
        logging.warning(f"Keynote page is empty")
        return
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("keynote", rc.prompt_group).format(
        response_format=generate_json_template(KeynotePageContentList),
    )
    llm = rc.default_model
    agent = create_agent(
        model=llm,
        system_prompt=prompt,   
        tools=[download_file_from_url],
        response_format=ToolStrategy(KeynotePageContentList)
    )
    response = await agent.with_retry().ainvoke(
        input=dict(
            messages=[HumanMessage(content=state["sections"].get(ConferenceFileNames.KEYNOTES_MD, ""))]
        )
    )
    structured_response: Optional[KeynotePageContentList] = response.get("structured_response")
    if not structured_response or not structured_response.items:
        logging.warning(f"LLM generate keynote response item is empty")
        return
    return dict(
        keynote_json=[
            KeynotePage(
                content=each
            ) for each in structured_response.items
        ]
    )


async def generate_topic_content_page(state: PPTState, config: RunnableConfig):
    md_content = state["sections"].get(ConferenceFileNames.TOPIC_MD, "")
    if not md_content:
        logging.warning(f"Topic content page is empty")
        return
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("default", rc.prompt_group).format(
        response_format=generate_json_template(TopicContentPageContent),
    )
    llm = rc.default_model
    agent = create_agent(
        model=llm,
        system_prompt=prompt,   
        tools=[download_file_from_url],
        response_format=ToolStrategy(TopicContentPageContent)
    )
    response = await agent.with_retry().ainvoke(
        input=dict(
            messages=[HumanMessage(content=state["sections"].get(ConferenceFileNames.TOPIC_MD, ""))]
        )
    )
    structured_response: Optional[TopicContentPageContent] = response.get("structured_response")
    if not structured_response:
        logging.warning(f"LLM generate topic content response is empty")
        return
    return dict(
        topic_content_json=TopicContentPage(
            content=structured_response
        )
    )


async def generate_topic_details_page(state: PPTState, config: RunnableConfig):
    md_content = state["sections"].get(ConferenceFileNames.TOPIC_MD, "")
    if not md_content:
        logging.warning(f"Topic details page is empty")
        return
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("topic_detail", rc.prompt_group).format(
        response_format=generate_json_template(TopicDetailPageContentList),
    )
    llm = rc.default_model
    agent = create_agent(
        model=llm,
        system_prompt=prompt,   
        tools=[download_file_from_url],
        response_format=ToolStrategy(TopicDetailPageContentList)
    )
    response = await agent.with_retry().ainvoke(
        input=dict(
            messages=[HumanMessage(content=state["sections"].get(ConferenceFileNames.TOPIC_MD, ""))]
        )
    )
    structured_response: Optional[TopicDetailPageContentList] = response.get("structured_response")
    if not structured_response:
        logging.warning(f"LLM generate topic details response is empty")
        return
    return dict(
        topic_details_json=[
            TopicDetailPage(
                content=each
            ) for each in structured_response.items
        ]
    )


async def generate_best_papers_page(state: PPTState, config: RunnableConfig):
    best_papers = state["sections"].get(ConferenceFolderNames.BEST_PAPERS, [])
    if not best_papers:
        logging.warning(f"Best papers is empty")
        return dict(
            best_papers_json=[]
        )
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("default", rc.prompt_group).format(
        response_format=generate_json_template(ValuablePaperPageContent),
    )
    llm = rc.default_model

    async def process_one_paper(paper_text: str):
        agent = create_agent(
            model=llm,
            system_prompt=prompt,   
            tools=[download_file_from_url],
            response_format=ToolStrategy(ValuablePaperPageContent)  
        )
        messages = [HumanMessage(content=paper_text)]

        try:
            response = await agent.with_retry().ainvoke(input={"messages": messages})
            structured = response.get("structured_response")
            if not structured:
                logging.warning(f"LLM generate best paper response is empty")
                return None

            return ValuablePaperPage(content=structured)
        except Exception as e:
            logging.error(f"[generate_best_papers_page] Error processing paper: {e}")
            return None

    tasks = [process_one_paper(bp) for bp in best_papers]
    results: List[ValuablePaperPage] = await asyncio.gather(*tasks, return_exceptions=False)
    results = [r for r in results if r is not None]
    return dict(
        best_papers_json=results
    )


async def generate_summary_page(state: PPTState, config: RunnableConfig):
    md_content = state["sections"].get(ConferenceFileNames.SUMMARY_MD, "")
    if not md_content:
        logging.warning(f"Summary page is empty")
        return
    rc = parse_research_config(config)
    prompt = rc.prompt_manager.get_prompt("default", rc.prompt_group).format(
        response_format=generate_json_template(ConfSummaryPageContent),
    )
    llm = rc.default_model
    agent = create_agent(
        model=llm,
        system_prompt=prompt,   
        tools=[download_file_from_url],
        response_format=ToolStrategy(ConfSummaryPageContent)
    )
    response = await agent.with_retry().ainvoke(
        input=dict(
            messages=[HumanMessage(content=state["sections"].get(ConferenceFileNames.SUMMARY_MD, ""))]
        )
    )
    structured_response = response.get("structured_response")
    if not structured_response:
        logging.warning(f"LLM generate Summary response is empty")
        return
    return dict(
        summary_json=ConfSummaryPage(
            content=structured_response
        )
    )


async def assemble_ppt_json(state: PPTState, config: RunnableConfig):
    if state.get("ppt_json") is not None:
        return state

    # ------------------ cover page ------------------
    cover = {
        "type": "cover_page",
        "content": {
            "conference_name": "",
            "date": ""
        }
    }

    # 获取当前时间
    now = datetime.now()
    formatted_date = now.strftime("%Y年%m月%d日 %H点%M分")
    
    ov: ConfOverviewPage = state.get("overview_json")
    if ov:
        cover["content"]["conference_name"] = ov.content.conf_name
        cover["content"]["date"] = formatted_date
    pages: List[Dict[str, Any]] = []
    pages.append(cover)

    # ------------------ skip_fill page ------------------
    pages.append({"type": "content_page", "skip_fill": True})

    # ------------------ overview page ------------------
    if ov is not None:
        pages.append(ov.model_dump(by_alias=True))


    # ------------------ 新增 8 个页面 ------------------
    new_pages_keys = [
        "tech_theme_page_json",
        "research_hotspot_collab_01_page_json",
        "research_hotspot_collab_02_page_json",
        "country_tech_feature_page_json",
        "institution_tech_feature_page_json",
        "institution_tech_strength_page_json",
        "institution_cooperation_page_json",
        "high_potential_tech_transfer_page_json",
    ]

    for key in new_pages_keys:
        page_obj = state.get(key)
        if page_obj is not None:
            pages.append(page_obj.model_dump(by_alias=True))

    # ------------------ keynotes ------------------
    if state.get("keynote_json") is not None:
        for each in state["keynote_json"]:
            pages.append(each.model_dump(by_alias=True))

    # ------------------ topic content ------------------
    if state.get("topic_content_json") is not None:
        pages.append(state["topic_content_json"].model_dump(by_alias=True))

    # ------------------ topic details ------------------
    for t in state.get("topic_details_json", []):
        pages.append(t.model_dump(by_alias=True))

    # ------------------ best papers ------------------
    for bp in state.get("best_papers_json", []):
        pages.append(bp.model_dump(by_alias=True))

    # ------------------ summary page ------------------
    if state.get("summary_json") is not None:
        pages.append(state["summary_json"].model_dump(by_alias=True))

    # ------------------ 保存 ppt_json ------------------
    state["ppt_json"] = pages

    # 将图片路径进行归一化，保证 PPT 模板服务可直接读取
    rc = parse_research_config(config)
    _normalize_image_paths_in_pages(pages, rc)
    time_for_filename = now.strftime("%Y%m%d%H%M%S")

    current_thread_work_root = os.path.join(rc.work_root, "conference_report_result", rc.thread_id)
    ppt_generate_file_name = os.path.join(
        current_thread_work_root,
        f"{ov.content.conf_name if ov else ''} 洞察报告-{time_for_filename}.pptx"
    )
    state["ppt_generate_file_name"] = ppt_generate_file_name
    return state


async def save_ppt_json(state: PPTState, config: RunnableConfig):
    rc = parse_research_config(config)
    current_thread_work_root = os.path.join(rc.work_root, "conference_report_result", rc.thread_id)
    os.makedirs(current_thread_work_root, exist_ok=True)
    path = os.path.join(current_thread_work_root, "ppt_content.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state["ppt_json"], f, ensure_ascii=False, indent=2)
    return dict(
        ppt_json_file_path=path,
    )


# 构建 graph
builder = StateGraph(PPTState)

builder.add_node(PPTGraphNodeType.CHECK_EXISTING_PPT, check_existing_ppt)
builder.add_node(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, load_conference_sections)
builder.add_node(PPTGraphNodeType.GENERATE_OVERVIEW_PAGE, generate_overview_page)
builder.add_node(PPTGraphNodeType.GENERATE_KEYNOTES_PAGE, generate_keynotes_page)
builder.add_node(PPTGraphNodeType.GENERATE_TOPIC_CONTENT_PAGE, generate_topic_content_page)
builder.add_node(PPTGraphNodeType.GENERATE_TOPIC_DETAILS_PAGE, generate_topic_details_page)
builder.add_node(PPTGraphNodeType.GENERATE_BEST_PAPERS_PAGE, generate_best_papers_page)
builder.add_node(PPTGraphNodeType.GENERATE_SUMMARY_PAGE, generate_summary_page)
builder.add_node(PPTGraphNodeType.ASSEMBLE_PPT_JSON, assemble_ppt_json)
builder.add_node(PPTGraphNodeType.SAVE_PPT_JSON, save_ppt_json)
builder.add_node(
    PPTGraphNodeType.GENERATE_TECH_THEME_PAGE,
    make_generate_page(
        TechThemePageContent,
        TechThemePage,
        md_filename="tech_topics.md",
        return_key="tech_theme_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_01_PAGE,
    make_generate_page(
        ResearchHotspotCollab01PageContent,
        ResearchHotspotCollab01Page,
        md_filename="research_hotspots.md",
        return_key="research_hotspot_collab_01_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_02_PAGE,
    make_generate_page(
        ResearchHotspotCollab02PageContent,
        ResearchHotspotCollab02Page,
        md_filename="research_hotspots.md",
        return_key="research_hotspot_collab_02_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_COUNTRY_TECH_FEATURE_PAGE,
    make_generate_page(
        CountryTechFeaturePageContent,
        CountryTechFeaturePage,
        md_filename="national_tech_profile.md",
        return_key="country_tech_feature_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_INSTITUTION_TECH_FEATURE_PAGE,
    make_generate_page(
        InstitutionTechFeaturePageContent,
        InstitutionTechFeaturePage,
        md_filename="institution_overview.md",
        return_key="institution_tech_feature_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_INSTITUTION_TECH_STRENGTH_PAGE,
    make_generate_page(
        InstitutionTechStrengthPageContent,
        InstitutionTechStrengthPage,
        md_filename="institution_overview.md",
        return_key="institution_tech_strength_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_INSTITUTION_COOPERATION_PAGE,
    make_generate_page(
        InstitutionCooperationPageContent,
        InstitutionCooperationPage,
        md_filename="inter_institution_collab.md",
        return_key="institution_cooperation_page_json"
    )
)

builder.add_node(
    PPTGraphNodeType.GENERATE_HIGH_POTENTIAL_TECH_TRANSFER_PAGE,
    make_generate_page(
        HighPotentialTechTransferPageContent,
        HighPotentialTechTransferPage,
        md_filename="high_potential_tech_transfer.md",
        return_key="high_potential_tech_transfer_page_json"
    )
)


# 添加边
builder.set_entry_point(PPTGraphNodeType.CHECK_EXISTING_PPT)


def after_check_exsiting_ppt(state: PPTState, config: RunnableConfig):
    ppt_json = state.get("ppt_json")
    if ppt_json is not None:
        return END
    else:
        return PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS


builder.add_conditional_edges(PPTGraphNodeType.CHECK_EXISTING_PPT, after_check_exsiting_ppt)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_OVERVIEW_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_KEYNOTES_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_TOPIC_CONTENT_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_TOPIC_DETAILS_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_BEST_PAPERS_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_SUMMARY_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_TECH_THEME_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_01_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_02_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_COUNTRY_TECH_FEATURE_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_INSTITUTION_TECH_FEATURE_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_INSTITUTION_TECH_STRENGTH_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_INSTITUTION_COOPERATION_PAGE)
builder.add_edge(PPTGraphNodeType.LOAD_CONFERENCE_SECTIONS, PPTGraphNodeType.GENERATE_HIGH_POTENTIAL_TECH_TRANSFER_PAGE)


builder.add_edge(PPTGraphNodeType.GENERATE_OVERVIEW_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_KEYNOTES_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_TOPIC_CONTENT_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_TOPIC_DETAILS_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_BEST_PAPERS_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_SUMMARY_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_TECH_THEME_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_01_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_RESEARCH_HOTSPOT_COLLAB_02_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_COUNTRY_TECH_FEATURE_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_INSTITUTION_TECH_FEATURE_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_INSTITUTION_TECH_STRENGTH_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_INSTITUTION_COOPERATION_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.GENERATE_HIGH_POTENTIAL_TECH_TRANSFER_PAGE, PPTGraphNodeType.ASSEMBLE_PPT_JSON)


builder.add_edge(PPTGraphNodeType.ASSEMBLE_PPT_JSON, PPTGraphNodeType.SAVE_PPT_JSON)
builder.add_edge(PPTGraphNodeType.SAVE_PPT_JSON, END)

graph = builder.compile()
