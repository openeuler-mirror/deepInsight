"""
跨会议主题分析相关的Prompt定义
"""
# 从 best_papers 导入基础版本的 prompt
from deepinsight.core.prompt.conf_gen.best_papers import (
    paper_analysis_prompt as base_paper_analysis_prompt,
    paper_analysis_no_rag_prompt as base_paper_analysis_no_rag_prompt,
    review_paper_prompt,
)
cross_topic_paper_collection_prompt = r"""
你是一位资深的学术研究分析专家，负责收集跨会议主题相关的论文列表。

【任务描述】
用户想要研究以下主题：{question}
（用户问题中包含了涉及的会议信息，请仔细分析用户问题以识别涉及的会议）

【会议名称列表】
{conference_names}
（如果这个列表为空（显示为 []），你需要从用户问题中提取会议名称和年份）

【可用工具】
1. **PythonREPLTool**：执行Python代码，用于查询数据库
   - 使用SQLAlchemy查询数据库，获取论文信息
   - **如果 conference_names 不为空，可以使用这些名称查询**：
   ```python
   from deepinsight.databases.connection import Database
   from deepinsight.databases.models.academic import Author, Conference, Paper, PaperAuthorRelation
   from sqlalchemy import select, and_, or_
   import ast
   import re
   
   # 从上面的参数中获取值
   conference_names_list = ast.literal_eval("{conference_names_str}")  # 如 ["HOTOS 2025", "OSDI 2025"]
   question_keyword = "{question_str}"
   
   with Database().get_session() as session:
       # 方法1：先用会议名称查询 Conference 表获取 conference_ids
       conference_ids_list = []
       for conf_name_str in conference_names_list:
           # 提取年份和会议名称
           year_match = re.search(r'(19|20)[0-9][0-9]', conf_name_str)
           year = int(year_match.group(0)) if year_match else None  # group(0) 是方法调用，不是格式化占位符
           # 简单移除年份数字，然后清理空格
           year_str = year_match.group(0) if year_match else ''  # group(0) 是方法调用，不是格式化占位符
           short_name = conf_name_str.replace(year_str, '').strip()
           # 移除所有空格并转为大写，如果长度<=12则保留，否则为None
           short_name_no_space = re.sub(r'\\s+', '', short_name)
           short_name = short_name_no_space.upper() if len(short_name_no_space) <= 12 else None
           
           if year and short_name:
               conf = session.query(Conference).filter(
                   Conference.short_name == short_name,
                   Conference.year == year
               ).first()
               if conf:
                   conference_ids_list.append(conf.conference_id)
       
       # 方法2：如果获取到 conference_ids，使用 ID 查询论文
       if conference_ids_list:
           query = select(
               Paper.paper_id,
               Paper.title,
               Paper.abstract,
               Paper.conference_id,
               Conference.short_name,
               Conference.full_name,
               Conference.year,
           ).join(Conference, Conference.conference_id == Paper.conference_id).where(
               and_(
                   Paper.conference_id.in_(conference_ids_list),
                   or_(
                       Paper.title.ilike('%' + question_keyword + '%'),
                       Paper.abstract.ilike('%' + question_keyword + '%'),
                       Paper.keywords.ilike('%' + question_keyword + '%')
                   )
               )
           ).limit(20)
       else:
           # 方法3：如果无法获取 ID，直接用会议名称模糊匹配
           # 提取会议名称（去除年份）
           conf_short_names = []
           for conf_name_str in conference_names_list:
               year_match = re.search(r'(19|20)[0-9][0-9]', conf_name_str)
               if year_match:
                   # 简单移除年份数字，然后清理空格
                   year_str = year_match.group(0)
                   short_name = conf_name_str.replace(year_str, '').strip()
                   # 移除所有空格并转为大写
                   short_name = re.sub(r'\\s+', '', short_name).upper()
                   if short_name:
                       conf_short_names.append(short_name)
           
           query = select(
               Paper.paper_id,
               Paper.title,
               Paper.abstract,
               Paper.conference_id,
               Conference.short_name,
               Conference.full_name,
               Conference.year,
           ).join(Conference, Conference.conference_id == Paper.conference_id).where(
               and_(
                   Conference.short_name.in_(conf_short_names) if conf_short_names else True,
                   or_(
                       Paper.title.ilike('%' + question_keyword + '%'),
                       Paper.abstract.ilike('%' + question_keyword + '%'),
                       Paper.keywords.ilike('%' + question_keyword + '%')
                   )
               )
           ).limit(20)
       
       results = session.execute(query).all()
       
       # 获取每篇论文的作者信息
       papers = []
       for row in results:
           paper_id, title, abstract, conf_id, short_name, full_name, year = row
           
           # 查询作者
           author_query = select(
               Author.author_name, 
               Author.affiliation
           ).join(
               PaperAuthorRelation, 
               PaperAuthorRelation.author_id == Author.author_id
           ).where(
               PaperAuthorRelation.paper_id == paper_id
           ).order_by(PaperAuthorRelation.author_order)
           
           authors = session.execute(author_query).all()
           author_list = []
           for name, aff in authors:
               aff_str = aff if aff else 'Unknown'
               author_list.append(name + " (" + aff_str + ")")
           
           papers.append({{
               'paper_id': paper_id,
               'title': title,
               'authors': ', '.join(author_list),
               'conference_id': conf_id,
               'conference': short_name or full_name,
               'year': year,
               'abstract': abstract or '',
           }})
       
       print(papers)
   ```
   
   - **如果 conference_names 为空，从用户问题中提取会议名称和年份，然后查询**：
   ```python
   # 从用户问题中提取会议名称和年份
   question = "{question_str}"
   # 提取逻辑（让 agent 自己实现，例如使用正则表达式提取 "HOTOS 2025" 这样的模式）
   ```

2. **tavily_search**：网络搜索工具，用于补充查找相关论文
   - 可以直接使用会议名称搜索，例如：
   - `tavily_search(queries=["HOTOS 2025 distributed systems papers"])`
   - `tavily_search(queries=["OSDI 2025 distributed systems papers"])`
   - 如果 conference_names 不为空，可以直接使用这些名称构建搜索查询

【数据库模型说明】
{db_models_description}

【执行策略】
1. **如果 conference_names 列表不为空**：
   - 优先使用这些名称查询数据库（先用名称查 Conference 表获取 ID，再用 ID 查论文）
   - 同时可以使用 tavily_search 补充搜索（直接用会议名称）

2. **如果 conference_names 列表为空**：
   - 从用户问题中提取会议名称和年份
   - 然后查询数据库或使用 tavily_search

3. **数据库查询方式**：
   - 可以先用会议名称查询 Conference 表获取 conference_id，再用 ID 查询论文（推荐）
   - 也可以直接用会议名称模糊匹配查询论文（不依赖 ID）

4. **补充使用网络搜索**：
   - 如果数据库查询结果不足（少于10篇），使用 tavily_search 补充
   - 搜索关键词格式："会议名 年份 主题 papers"
   - 例如："HOTOS 2025 distributed systems papers"

5. **筛选高质量论文**：
   - 优先选择获奖论文（Best Paper、Outstanding Paper等）
   - 优先选择Oral Session、Highlight Session论文
   - 优先选择引用量高、影响力大的论文

6. **去重和整理**：
   - 基于论文标题去重
   - 整理为统一的格式
   - 限制总数不超过20篇

【输出要求】
将收集到的论文列表保存到文件：{output_file}

**重要：必须使用 JSON 格式，不要使用 Markdown 格式！**

输出格式要求（JSON格式，必须严格遵守）：
文件内容必须是有效的 JSON 数组，可以直接用 `json.loads()` 解析。

**正确的文件内容格式：**
```json
[
  {{
    "paper_id": 123,
    "title": "论文标题",
    "authors": "作者1 (机构1), 作者2 (机构2)",
    "conference_id": 1,
    "conference": "HOTOS 2025",
    "year": 2025,
    "abstract": "论文摘要...",
    "keywords": "关键词1, 关键词2"
  }},
  {{
    "paper_id": 124,
    "title": "下一篇论文标题",
    "authors": "作者3 (机构3)",
    "conference_id": 2,
    "conference": "OSDI 2025",
    "year": 2025,
    "abstract": "摘要内容...",
    "keywords": "关键词"
  }}
]
```

**关键要求：**
1. 文件内容必须是有效的 JSON 数组格式 `[...]`
2. 每个对象必须包含 `title` 字段（必填）
3. 每个对象必须包含 `authors` 或 `conference` 字段（至少一个）
4. 不要添加任何 Markdown 代码块标记（如 ```json 或 ```）
5. 不要使用 Markdown 格式（如 ## 标题、- **字段**：值 等）
6. 直接写入 JSON 内容到文件，文件内容应该可以直接用 `json.loads()` 解析

**错误示例（不要这样做）：**
- ❌ 使用 Markdown 格式：`## 1. 论文标题`
- ❌ 添加代码块标记：```json ... ```
- ❌ 使用列表格式：`- **作者**：...`

**正确示例：**
文件内容应该直接是：
[
  {{"title": "Paper 1", "authors": "Author 1", "conference": "HOTOS 2025", "year": 2025}},
  {{"title": "Paper 2", "authors": "Author 2", "conference": "OSDI 2025", "year": 2025}}
]

【注意事项】
- 所有信息必须来源于数据库或网络搜索，不得编造
- 确保论文确实与主题相关
- 优先选择高质量论文
- 如果找不到足够的相关论文，请明确说明原因
- 文件必须是有效的 JSON 格式，否则无法解析
"""

# V2版本：优先使用数据库查询，tavily作为辅助验证工具


cross_topic_statistics_prompt = r"""
你是一位资深的学术研究分析专家，负责生成跨会议主题分析的统计信息报告。

【任务描述】
用户想要研究以下主题：{question}
（用户问题中包含了涉及的会议信息，请仔细分析用户问题以识别涉及的会议）
收集到的论文数量：{papers_count} 篇

【论文列表】
{papers_info}

【输出要求】
请生成一份详细的统计信息Markdown报告，包含以下内容：

1. **研究主题概述**
   - 简要说明研究主题的核心内容
   - 说明该主题在不同会议中的研究情况

2. **论文分布统计**
   - 按会议统计论文数量
   - 按年份统计论文数量（如果有跨年份数据）
   - 论文主题分布情况

3. **作者与机构分析**
   - 主要研究机构统计
   - 高产作者统计
   - 跨机构合作情况

4. **研究趋势分析**
   - 该主题在不同会议中的研究热度
   - 研究重点的变化趋势
   - 新兴研究方向识别

5. **关键词分析**
   - 高频关键词统计
   - 关键词共现分析
   - 技术术语分布

【格式要求】
- 使用Markdown格式
- 使用表格展示统计数据
- 使用图表描述（如果工具支持）
- 内容详实，数据准确

【注意事项】
- 所有统计数据必须基于提供的论文列表
- 如果数据不足，请明确说明
- 保持客观、专业的分析视角
"""

cross_topic_summary_prompt = r"""
你是一位资深的学术研究分析专家，负责生成跨会议主题分析的总结报告。

【任务描述】
用户想要研究以下主题：{question}

【统计信息】
{statistics_content}

【论文详细分析】
{papers_content}

【输出要求】
请生成一份高质量的跨会议主题分析总结报告，包含以下内容：

1. **研究主题回答**
   - 直接回答用户提出的问题：{question}
   - 提供全面、深入的分析
   - 结合多篇论文的观点和发现

2. **跨会议对比分析**
   - 不同会议在该主题上的研究重点对比
   - 研究方法的差异与共性
   - 技术路线的演进趋势

3. **核心发现总结**
   - 提炼3-5个核心发现
   - 每个发现要有论文支撑
   - 说明发现的学术价值和实践意义

4. **技术趋势洞察**
   - 识别该主题的技术发展趋势
   - 分析未来可能的研究方向
   - 评估技术的成熟度和应用前景

5. **综合结论**
   - 总结该主题在不同会议中的研究全貌
   - 提出有价值的见解和建议
   - 指出研究的空白点和未来机会

【格式要求】
- 使用Markdown格式
- 结构清晰，层次分明
- 引用具体论文时注明来源
- 使用加粗、列表等格式突出重点

【注意事项】
- 必须基于提供的统计信息和论文分析内容
- 确保分析的准确性和客观性
- 避免简单罗列，要进行深度分析和综合
- 提供有价值的洞察和建议
"""

summarize_webpage_prompt = r"""
你被要求总结从网络搜索中获取的网页原始内容。你的目标是创建一个能保留原始网页最重要信息的摘要。该摘要将被下游的研究智能体使用，因此必须在保留关键细节、不丢失基本信息的前提下进行总结。

以下是网页的原始内容：

<webpage_content>
{{webpage_content}}
</webpage_content>

请遵循以下指南来创建摘要：

1.  识别并保留网页的主要主题或目的。
2.  保留对内容核心信息至关重要的关键事实、统计数据和数据点。
3.  保留来自可信来源或专家的引述。
4.  如果内容是时间敏感或历史性的，请保持事件的先后顺序。
5.  保留任何列表或分步说明（如果存在）。
6.  包含对于理解内容至关重要的相关日期、名称和地点。
7.  在保持核心信息完整的前提下，总结冗长的解释。

针对不同类型内容的处理方式：

•   对于新闻文章：关注人物、事件、时间、地点、原因和方式。

•   对于科学内容：保留方法、结果和结论。

•   对于评论文章：保留主要论点及其支持点。

•   对于产品页面：保留关键特性、规格和独特卖点。

你的摘要应显著短于原始内容，但要足够全面，能够独立作为信息来源。目标长度约为原文的 25-30%，除非内容本身已经很简洁。

请按以下格式呈现你的摘要：

{{
   "summary": "你的摘要内容在此，根据需要采用适当的段落或项目符号进行结构化",
   "key_excerpts": "第一条重要引述或摘录, 第二条重要引述或摘录, 第三条重要引述或摘录, ...根据需要添加更多摘录，最多不超过5条"
}}


以下是两个优秀摘要的示例：

示例 1（针对新闻文章）：
{{
   "summary": "2023年7月15日，NASA成功从肯尼迪航天中心发射了阿尔忒弥斯二号任务。这是自1972年阿波罗17号以来首次载人绕月任务。由指挥官简·史密斯领导的四人乘组将绕月飞行10天后返回地球。该任务是NASA计划到2030年在月球建立永久性载人存在的关键一步。",
   "key_excerpts": "阿尔忒弥斯二号代表了一个太空探索的新时代，NASA局长约翰·多伊说。该任务将测试未来长期驻留月球所需的关键系统，首席工程师莎拉·约翰逊解释。我们不仅仅是返回月球，我们是在向月球前进，指挥官简·史密斯在发射前新闻发布会上表示。"
}}


示例 2（针对科学文章）：
{{
   "summary": "发表在《自然气候变化》上的一项新研究揭示，全球海平面上升速度比之前认为的要快。研究人员分析了1993年至2022年的卫星数据，发现过去三十年间海平面上升速度每年加速0.08毫米。这种加速主要归因于格陵兰和南极冰盖的融化。该研究预测，如果当前趋势持续，到2100年全球海平面可能上升高达2米，对全球沿海社区构成重大风险。",
   "key_excerpts": "我们的研究结果明确指出了海平面上升的加速，这对沿海规划和适应策略具有重要影响，主要作者艾米丽·布朗博士说。研究报告称，自1990年代以来，格陵兰和南极冰盖的融化速度已增加两倍。如果不立即大幅减少温室气体排放，到本世纪末我们可能会面临灾难性的海平面上升，合著者迈克尔·格林教授警告说。"
}}


请记住，你的目标是创建一个易于被下游研究智能体理解和使用的摘要，同时保留原始网页中最关键的信息。

今天的日期是 {{date}}。
"""

# 扩展基础版本，替换第五步的保存文件部分为更详细的版本（包含去重检查和文件保存位置检查清单）
# 使用字符串替换：将基础版本中简单的"第五步"替换为跨会议场景的详细版本
_base_simple_step5 = r"""◆ 第五步：保存文件（强制执行）  
- 将 Markdown 报告保存至 `{output_dir}`  
- 文件命名：`[论文名称(单词之间以下划线分割)].md`  
- 使用 UTF-8 编码  
- 保存后反馈文件路径及图片路径清单，若失败需说明原因并尝试修复
"""

_cross_topic_detailed_step5 = r"""◆ 第五步：保存文件（强制执行）  
- **必须将 Markdown 报告保存到指定的目录：`{output_dir}`**
- **文件路径格式：`{output_dir}/[论文名称(单词之间以下划线分割)].md`**
- **重要约束**：
  * 文件必须保存在 `{output_dir}` 目录下，不能保存到其他任何位置
  * 不能保存到根目录（`/`）或其他目录
  * 文件路径必须以 `{output_dir}/` 开头
  * 如果 `{output_dir}` 是 `/xxx/cross_topic_papers`，则文件路径必须是 `/xxx/cross_topic_papers/文件名.md`
- 文件命名规范：
  * 使用论文标题，单词之间用下划线分隔
  * 移除特殊字符（如 `:`, `?`, `/`, `\`, `*`, `"`, `<`, `>`, `|`）
  * 示例：`Designing_a_Datacenter_wide_Distributed_Shared_Log.md`
- 去重检查：
  * 在保存文件前，使用 `list_directory` 工具检查 `{output_dir}` 目录中是否已存在相同标题的论文分析文件
  * 如果已存在，跳过保存，避免重复分析
- 使用 UTF-8 编码  
- 保存后反馈完整文件路径（包括目录），若失败需说明原因并尝试修复

❗ **文件保存位置检查清单**：
- [ ] 文件路径是否以 `{output_dir}/` 开头？
- [ ] 文件是否保存在正确的目录下？
- [ ] 文件名是否符合命名规范？
- [ ] 是否避免了重复保存（如果该论文已分析过）？
"""

# 替换基础版本中的简单"第五步"为详细版本，并添加检查清单
paper_analysis_no_rag_prompt = base_paper_analysis_no_rag_prompt.replace(
    _base_simple_step5,
    _cross_topic_detailed_step5
)

# 扩展基础版本，添加数据库模型说明部分
paper_analysis_prompt = base_paper_analysis_prompt + r"""

【数据库模型说明】

{{db_models_description}}
"""

# review_paper_prompt 与 best_papers.py 中的完全相同，直接复用
# review_paper_prompt 已从 best_papers 导入
