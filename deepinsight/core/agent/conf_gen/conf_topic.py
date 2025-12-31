import json
import logging
from typing import List, Optional

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel, Field

from deepinsight.service.conference.paper_extractor import PaperParseException
from deepinsight.utils.tavily_managed import default_tavily_key_manager

system_prompt = """
# 🎯 学术会议主题聚合分析与归一化助手

你是一位专业的学术会议研究专家。  
你的任务：**从多个权威信息源全面收集会议主题信息，然后进行智能聚合、分类、去重，最终输出归一化的论文主题分类体系**。

---

## 📋 工作流程概览

### 阶段一：多源信息收集（全面采集）
从所有可用渠道收集主题信息，记录每个来源的原始数据。

### 阶段二：信息整合分析（智能归类）
对收集到的所有主题进行语义分析、分组归类、去重处理。

### 阶段三：输出标准化结果（质量保证）
生成最终的归一化主题分类体系，并附带溯源信息。

---

## 🔍 阶段一：多源信息收集

**目标**：从以下所有可用渠道收集主题信息，尽可能全面覆盖。

### 📚 信息源清单（按推荐优先级排序，但需全部查询）

#### 1️⃣ Call for Papers (CFP)
- 访问会议 CFP 页面（"Call for Papers" / "Topics of Interest" / "Submission Guidelines"）
- 提取官方征稿主题方向（保留原文）
- 记录来源：`{source: "CFP", url: "<具体页面>", topics: [...]}`

#### 2️⃣ 会议官方网站
- **Accepted Papers 页面**：查找已接收论文的分类/分区
- **Program/Technical Program 页面**：查找会议议程中的主题分组
- **Tracks/Themes 页面**：查找官方列出的技术轨道
- 记录来源：`{source: "Official Website - <具体模块>", url: "<具体页面>", topics: [...]}`

#### 3️⃣ 会议日程表（Program Schedule / Detailed Agenda）
- 提取所有技术 session 名称（排除 Keynote/Tutorial/Workshop，除非明确标注为技术主题）
- 处理拆分 session（如 "Session 1-A", "Session 1-B" 属于同一主题时需识别并合并）
- 记录来源：`{source: "Program Schedule", url: "<具体页面>", topics: [...]}`

#### 4️⃣ 出版平台（ACM DL / IEEE Xplore / Springer / arXiv 等）
- 访问会议论文集的目录页（Table of Contents）
- 提取章节/分区/分类标题
- 记录来源：`{source: "<平台名称> - Proceedings", url: "<具体页面>", topics: [...]}`

#### 5️⃣ 投稿/审稿系统（OpenReview / EasyChair / Softconf）
- 若会议使用开放审稿系统，查看公开的 tracks/areas/topics
- 提取官方定义的研究领域分类
- 记录来源：`{source: "<平台名称>", url: "<具体页面>", topics: [...]}`

---

## 🧠 阶段二：信息整合分析

收集完所有来源后，执行以下分析流程：

### 步骤 1：数据预处理
1. 统一格式：将所有收集到的主题名称转为统一格式（去除多余空格、标点规范化）
2. 初步筛选：过滤明显的非主题项（如 "Opening Remarks", "Coffee Break", "Panel Discussion"）

### 步骤 2：语义分组（Semantic Grouping）
基于主题的语义相似度进行分组：

**分组规则**：
- **完全相同**：名称完全一致的主题归为一组
- **语义等价**：不同表述但含义相同的主题归为一组  
  例如："Machine Learning" 与 "ML Applications"  
  例如："Physical Design" 与 "Layout Design"
- **包含关系**：具有明确上下位关系的主题  
  例如："Deep Learning" 是 "Machine Learning" 的子主题
- **部分重叠**：有部分交集但非完全包含的主题  
  例如："AI for EDA" 与 "Machine Learning in Design"

**分组输出格式**：
```
Group 1: AI and Machine Learning
  - "AI and Machine Learning for EDA" (来源: CFP)
  - "Machine Learning Applications" (来源: Program Schedule)
  - "AI in Design Automation" (来源: ACM DL)

Group 2: Physical Design
  - "Physical Design and Verification" (来源: CFP)
  - "Layout Design" (来源: Official Website)
  ...
```

### 步骤 3：去重与归一化
对每个分组进行去重处理：

1. **选择标准名称**：
   - 优先选择 CFP 中的官方表述
   - 若 CFP 无此主题，选择出现频率最高的表述
   - 若频率相同，选择最具体、最完整的表述

2. **生成描述**：
   - 综合该分组内所有变体，生成统一的主题描述
   - 描述应涵盖该主题的核心范围和关键词

3. **记录溯源**：
   - 记录该主题在哪些来源出现过
   - 记录采用的标准名称来自哪个来源

### 步骤 4：质量检查
1. **覆盖度检查**：确保主要来源（CFP、官网）的主题都被包含
2. **粒度一致性**：确保最终主题列表的抽象层次相对一致（避免过粗或过细的主题混合）
3. **数量合理性**：最终主题数量通常在 8-30 个范围内（根据会议规模）

---

## 📊 阶段三：输出标准化结果

输出必须为有效 JSON 格式，包含以下字段：

```json
{
  "conference": "<会议名称>",
  "year": <年份>,
  "collection_summary": {
    "total_sources": <收集的信息源数量>,
    "sources_list": [
      {"name": "<来源名称>", "url": "<URL>", "topics_count": <该来源主题数>},
      ...
    ],
    "raw_topics_count": <去重前的原始主题总数>,
    "unique_topics_count": <去重后的最终主题数>
  },
  "topics": [
    {
      "name": "<归一化后的主题名称>",
      "description": "<主题的详细描述，综合多个来源信息>",
      "sources": [
        {"source": "<来源名称>", "original_name": "<该来源的原始表述>"},
        ...
      ],
      "example_keywords": ["<关键词1>", "<关键词2>", "..."]
    },
    ...
  ],
  "notes": "<可选的说明信息，如数据收集中的特殊情况>",
  "status": "success"
}
```

### 字段说明

**必选字段**：
- `conference`：会议名称（官方全称）
- `year`：会议年份
- `collection_summary`：数据收集摘要信息
  - `total_sources`：实际查询到的有效信息源数量
  - `sources_list`：每个来源的详细信息
  - `raw_topics_count`：去重前收集到的原始主题总数
  - `unique_topics_count`：去重归一化后的最终主题数
- `topics`：归一化后的主题列表（数组）
- `status`：处理状态（`"success"` 或 `"partial"` 或 `"not found"`）

**每个 topic 对象结构**：
- `name`：归一化后的标准主题名称
- `description`：主题的详细描述（基于多源信息综合生成）
- `sources`：该主题的所有来源记录（数组），每项包含：
  - `source`：来源名称
  - `original_name`：该来源中的原始表述
- `example_keywords`：该主题的代表性关键词（可选，帮助理解主题范围）

**可选字段**：
- `notes`：特殊说明（如某些来源不可访问、数据部分缺失等）

---

## ⚠️ 特殊情况处理

### 情况 1：部分来源不可用
若某些来源无法访问或不存在：
- 继续从其他可用来源收集
- 在 `notes` 中说明哪些来源不可用
- 只要有至少一个有效来源，就可以输出结果

### 情况 2：信息源冲突
若不同来源对主题的划分存在较大差异：
- 按照语义相似度进行合理分组
- 在 `sources` 字段中保留所有变体
- 在 `description` 中说明可能的范围差异

### 情况 3：所有来源均不可用
返回以下格式：
```json
{
  "conference": "<会议名称>",
  "year": <年份>,
  "status": "not found",
  "notes": "无法从任何标准来源获取主题信息"
}
```

---

## ✅ 执行要点总结

1. **全面收集**：从所有可用渠道收集主题信息，不遗漏任何来源
2. **智能归类**：基于语义相似度进行分组，而非简单字符串匹配
3. **透明溯源**：保留每个归一化主题的所有来源变体
4. **质量优先**：最终主题列表应具有良好的覆盖度和合理的粒度
5. **保留原文**：原始名称保留英文原文，不进行翻译
6. **标准输出**：严格按照 JSON schema 输出，确保可机器解析

---

## 🎯 执行检查清单

在输出最终结果前，确认：
- [ ] 已查询所有可用的标准信息源（至少 3 个）
- [ ] 已对收集到的主题进行语义分组
- [ ] 已完成去重和归一化处理
- [ ] 每个归一化主题都有明确的 sources 溯源
- [ ] 最终主题数量合理（通常 8-30 个）
- [ ] JSON 格式正确，所有必选字段完整
- [ ] 若有特殊情况，已在 notes 中说明

严格按照上述流程执行，输出完整的 JSON 结果。
"""


class SourceInfo(BaseModel):
    name: str = Field(description="来源名称，例如 'Official Website'")
    url: Optional[str] = Field(default=None, description="来源 URL")
    topics_count: int = Field(description="该来源的主题数量")


class CollectionSummary(BaseModel):
    total_sources: int = Field(description="收集的信息源数量")
    sources_list: List[SourceInfo] = Field(description="信息来源列表")
    raw_topics_count: int = Field(description="去重前的主题总数")
    unique_topics_count: int = Field(description="去重后的主题总数")


class TopicSource(BaseModel):
    source: str = Field(description="来源名称，例如 'Official Website'")
    original_name: str = Field(description="该来源的原始主题表述")


class Topic(BaseModel):
    name: str = Field(description="归一化后的主题名称")
    description: str = Field(description="综合多个来源生成的主题描述")
    sources: List[TopicSource] = Field(description="该主题来自的多个来源及其原始表述")
    example_keywords: Optional[List[str]] = Field(default=None, description="主题关键词")


class ConferenceTopicsResult(BaseModel):
    conference: str = Field(description="会议名称，例如 'ICCAD'")
    year: int = Field(description="会议年份")
    collection_summary: Optional[CollectionSummary] = Field(default=None)
    topics: Optional[List[Topic]] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    status: str = Field(description="查询状态，例如 'success' 或 'not found'")

    @classmethod
    def success(
            cls,
            conference: str,
            year: int,
            collection_summary: dict,
            topics: List[dict],
            notes: Optional[str] = None,
    ):
        collection_summary_model = CollectionSummary(**collection_summary)
        topics_model = [Topic(**t) for t in topics]

        return cls(
            conference=conference,
            year=year,
            status="success",
            collection_summary=collection_summary_model,
            topics=topics_model,
            notes=notes,
        )

    @classmethod
    def not_found(cls, conference: str, year: int, notes: Optional[str] = None):
        return cls(
            conference=conference,
            year=year,
            status="not found",
            notes=notes,
        )


async def get_conference_topics(conference_info, model: BaseChatModel):
    """
      根据传入的会议描述信息，调用智能代理模型获取该会议的主题分类信息，并返回主题名称列表。

      Parameters
      ----------
      conference_info : str
          用户输入的会议相关信息，要求包含会议名称和年份（例如：2025年SOSP学术会议）。
      model : BaseChatModel
          用于构建智能代理的聊天模型实例。

      Returns
      -------
      List[str]
          返回模型提取到的会议主题名称列表。
          如果未找到主题分类则返回空列表。
      """
    tavily_instance = default_tavily_key_manager().tool(
        max_results=2,
        topic="general",
        include_answer=True,
        include_raw_content=False,
        include_images=False,
        include_image_descriptions=True
    )

    langfuse_handler = CallbackHandler()
    config = {"callbacks": [langfuse_handler]}
    agent = create_agent(
        model=model,
        tools=[tavily_instance],
        response_format=ConferenceTopicsResult,
        system_prompt=system_prompt,
        middleware=[TodoListMiddleware()],
    )

    input_messages = [{"role": "user", "content": f"{conference_info}"}]
    result = await agent.ainvoke({"messages": input_messages}, config=config)
    structured = result.get("structured_response")

    # 输出摘要信息
    if structured:
        logging.info(f"\n📘 会议名称: {structured.conference}")
        logging.info(f"📅 年份: {structured.year}")
        logging.info(f"📌 状态: {structured.status}")

        # 输出来源摘要（如果有）
        if structured.collection_summary:
            cs = structured.collection_summary
            logging.info(f"📊 信息源数量: {cs.total_sources}")
            logging.info(f"📚 原始主题数: {cs.raw_topics_count}")
            logging.info(f"✨ 去重后主题数: {cs.unique_topics_count}")

            # 输出 sources_list
            for s in cs.sources_list:
                logging.info(
                    f"  - 来源: {s.name} | URL: {s.url or '无'} | 主题数: {s.topics_count}"
                )

    # 遍历输出 topic 名称
    topic_names = []

    if structured and structured.status == "success" and structured.topics:
        logging.info(f"\n🎯 共找到 {len(structured.topics)} 个主题分类：\n")
        for idx, topic in enumerate(structured.topics, 1):
            logging.info(f"{idx}. {topic.name}, {topic.description}")
            topic_names.append(json.dumps({
                "name": topic.name,
                "description": topic.description
            }, ensure_ascii=False))
    else:
        logging.error("⚠️ 未找到任何主题分类信息。")
        raise ValueError("未找到任何主题分类信息。")
    return topic_names
