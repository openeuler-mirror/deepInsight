"""
跨会议主题论文收集Agent

使用PythonREPLTool和tavily_search工具，通过deepagent和提示词策略来控制论文收集。
"""
import logging
from typing import List, Dict
from langchain_core.runnables import RunnableConfig
from langchain_experimental.tools import PythonREPLTool
from langchain.agents.middleware import ModelFallbackMiddleware
from deepagents import create_deep_agent
from langfuse.langchain import CallbackHandler

from deepinsight.core.tools.tavily_search import tavily_search
from deepinsight.core.tools.file_system import register_fs_tools, MemoryMCPFilesystem
from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.core.utils.tool_utils import CoerceToolOutput
from deepinsight.utils.db_schema_utils import get_db_models_source_markdown

logger = logging.getLogger(__name__)


async def collect_papers_for_topic(
    question: str,
    kb_ids: List,  # 支持 List[int]（CLI版本）或 List[str]（Web版本）
    output_file: str,
    config: RunnableConfig,
    conference_names: List[str] = None,  # 可选的会议名称列表，如 ["HOTOS 2025", "OSDI 2025"]
) -> List[Dict[str, str]]:
    """
    使用deepagent收集与主题相关的论文列表
    
    Args:
        question: 研究问题/主题（包含会议名称信息）
        kb_ids: 知识库ID列表（CLI版本是整数列表，Web版本可能是字符串列表）
        output_file: 输出文件路径（用于保存论文列表）
        config: 运行时配置
        conference_names: 可选的会议名称列表（CLI版本可以从参数中提取，Web版本可以为空）
    
    Returns:
        论文信息列表
    """
    rc = parse_research_config(config)
    fs_instance = MemoryMCPFilesystem()
    tools = register_fs_tools(fs_instance)
    
    # 使用现有的工具
    tools.append(PythonREPLTool())
    tools.append(tavily_search)
    
    # 如果没有提供 conference_names，设置为空列表（让 agent 自己从问题中提取）
    if conference_names is None:
        conference_names = []
    
    # 使用正式的 prompt，确保格式要求明确
    conference_names_str = ", ".join(conference_names) if conference_names else "[]"
    question_str = question
    
    # 从 prompt_manager 获取正式的 prompt
    prompt_template = rc.prompt_manager.get_prompt(
        name="cross_topic_paper_collection_prompt",
        group=rc.prompt_group,
    ).format(
        question=question,
        question_str=question_str,
        conference_names=conference_names_str if conference_names else "[]",
        conference_names_str=conference_names_str if conference_names else "[]",
        output_file=output_file,
        db_models_description=get_db_models_source_markdown(),
    )
    
    middleware = [
        CoerceToolOutput(),
        ModelFallbackMiddleware(
            rc.default_model,
            rc.default_model,
        )
    ]
    
    agent = create_deep_agent(
        model=rc.default_model,
        tools=tools,
        system_prompt=prompt_template,
        middleware=middleware,
    )
    
    user_message = f"请收集与主题'{question}'相关的论文列表，并保存到文件：{output_file}"
    
    logger.info(f"开始执行论文收集 agent，输出文件: {output_file}")
    logger.info(f"可用工具: {[tool.name if hasattr(tool, 'name') else str(tool) for tool in tools]}")
    
    # 添加 Langfuse 追踪
    # 使用 .with_config 方式传递 callbacks（类似 ror.py 中的用法）
    langfuse_handler = CallbackHandler()
    config_dict = dict(config) if not isinstance(config, dict) else config
    config_dict = {**config_dict, "recursion_limit": 300}
    
    # 尝试使用 .with_config 方式传递 callbacks
    try:
        agent_with_callbacks = agent.with_config(
            run_name="collect_papers_for_topic",
            callbacks=[langfuse_handler]
        )
        logger.info("使用 with_config 方式传递 callbacks")
        result = await agent_with_callbacks.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
        logger.info(f"Agent 执行完成，结果类型: {type(result)}, 结果: {str(result)[:200] if result else 'None'}")
    except (AttributeError, TypeError) as e:
        # 如果 with_config 不支持，尝试直接传递（可能不兼容）
        logger.warning(f"无法使用 with_config 传递 callbacks: {e}，尝试直接传递")
        try:
            if "callbacks" not in config_dict:
                config_dict["callbacks"] = []
            elif not isinstance(config_dict["callbacks"], list):
                config_dict["callbacks"] = [config_dict["callbacks"]]
            config_dict["callbacks"].append(langfuse_handler)
            logger.info("使用直接传递 callbacks 方式")
            result = await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
            logger.info(f"Agent 执行完成（带 callbacks），结果类型: {type(result)}, 结果: {str(result)[:200] if result else 'None'}")
        except Exception as e2:
            logger.warning(f"无法添加 Langfuse 追踪: {e2}，跳过追踪继续执行")
            result = await agent.ainvoke({"messages": [{"role": "user", "content": user_message}]}, config=config_dict)
            logger.info(f"Agent 执行完成（无 callbacks），结果类型: {type(result)}, 结果: {str(result)[:200] if result else 'None'}")
    except Exception as e:
        logger.error(f"Agent 执行时发生异常: {e}", exc_info=True)
        raise
    
    # 检查文件系统状态
    logger.info(f"检查文件系统状态，文件列表: {list(fs_instance.files.keys())}")
    logger.info(f"目录列表: {list(fs_instance.dirs)}")
    
    # 读取生成的文件，解析论文列表
    content = fs_instance.read_file(output_file)
    
    if not content:
        logger.warning(f"文件 {output_file} 为空，agent 可能没有成功生成文件")
        # 尝试列出所有文件，看看是否有其他文件
        all_files = fs_instance.list_directory("/")
        logger.info(f"内存文件系统中的文件: {all_files}")
        return []
    
    logger.info(f"读取到文件内容，长度: {len(content)} 字符")
    logger.debug(f"文件内容前500字符: {content[:500]}")
    
    # 解析文件内容，提取论文信息
    # conference_info_list 可以为空，让 agent 自己匹配会议信息
    conference_info_list = []
    papers = _parse_papers_from_content(content, conference_info_list)
    
    if not papers:
        logger.warning(f"未能从文件内容中解析出论文列表。文件内容: {content[:1000]}")
    
    return papers


def _parse_papers_from_content(content: str, conference_info_list: List[Dict]) -> List[Dict[str, str]]:
    """
    从生成的文件内容中解析论文列表
    只支持 JSON 格式，更稳定可靠
    
    Args:
        content: 文件内容（必须是 JSON 格式）
        conference_info_list: 会议信息列表（可以为空，Web版本场景）
    
    Returns:
        论文信息列表
    """
    papers = []
    
    if not content or not content.strip():
        logger.warning("文件内容为空")
        return papers
    
    # 创建会议ID到会议信息的映射（如果 conference_info_list 为空，conf_map 也为空）
    conf_map = {info['conference_id']: info for info in conference_info_list} if conference_info_list else {}
    
    import json
    import re
    
    # 1. 尝试提取 JSON 代码块（如果 agent 添加了代码块标记）
    json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
        try:
            papers = json.loads(json_str)
            if isinstance(papers, list):
                logger.info(f"从 JSON 代码块中解析出 {len(papers)} 篇论文")
                return _validate_and_filter_papers(papers, conf_map)
            elif isinstance(papers, dict) and 'papers' in papers:
                papers = papers['papers']
                logger.info(f"从 JSON 代码块的 papers 字段中解析出 {len(papers)} 篇论文")
                return _validate_and_filter_papers(papers, conf_map)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 代码块解析失败: {e}")
    
    # 2. 尝试直接解析整个内容为 JSON（移除可能的代码块标记）
    try:
        cleaned_content = content.strip()
        # 移除可能的代码块标记
        if cleaned_content.startswith('```'):
            cleaned_content = re.sub(r'^```(?:json)?\s*', '', cleaned_content, flags=re.MULTILINE)
            cleaned_content = re.sub(r'\s*```$', '', cleaned_content, flags=re.MULTILINE)
            cleaned_content = cleaned_content.strip()
        
        data = json.loads(cleaned_content)
        if isinstance(data, list):
            papers = data
            logger.info(f"从内容中直接解析出 {len(papers)} 篇论文")
            return _validate_and_filter_papers(papers, conf_map)
        elif isinstance(data, dict) and 'papers' in data:
            papers = data['papers']
            logger.info(f"从字典的 papers 字段中解析出 {len(papers)} 篇论文")
            return _validate_and_filter_papers(papers, conf_map)
    except json.JSONDecodeError as e:
        logger.warning(f"直接 JSON 解析失败: {e}")
    
    # 3. 如果都失败，尝试提取 JSON 数组（使用正则匹配）
    json_match = re.search(r'(\[.*?\])', content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
        try:
            papers = json.loads(json_str)
            if isinstance(papers, list):
                logger.info(f"从正则匹配的 JSON 中解析出 {len(papers)} 篇论文")
                return _validate_and_filter_papers(papers, conf_map)
        except json.JSONDecodeError as e:
            logger.warning(f"正则匹配的 JSON 解析失败: {e}")
    
    # 4. 如果所有 JSON 解析都失败，尝试降级解析 Markdown 格式（作为最后手段）
    logger.warning("JSON 解析失败，尝试降级解析 Markdown 格式")
    papers = _try_parse_markdown_fallback(content)
    if papers:
        logger.info(f"从 Markdown 格式（降级解析）中解析出 {len(papers)} 篇论文")
        return _validate_and_filter_papers(papers, conf_map)
    
    # 如果所有解析都失败，记录错误
    logger.error(f"无法解析论文列表：文件内容既不是有效的 JSON 格式，也无法解析为 Markdown。内容预览: {content[:500]}")
    return []


def _try_parse_markdown_fallback(content: str) -> List[Dict]:
    """
    降级解析：尝试从 Markdown 格式中解析论文列表（作为最后手段）
    支持格式：### N. Title 或 ## N. Title，以及 **字段名**: 值
    """
    import re
    papers = []
    lines = content.split('\n')
    current_paper = {}
    current_field = None
    in_paper_section = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 检查是否是论文标题（### N. Title 或 ## N. Title）
        title_match = re.match(r'^#{2,3}\s*\d+\.\s*(.+)$', line)
        if title_match:
            # 保存上一篇论文
            if current_paper and 'title' in current_paper:
                papers.append(current_paper)
            current_paper = {'title': title_match.group(1).strip()}
            current_field = None
            in_paper_section = True
            continue
        
        # 检查是否是字段行（**字段名**: 值）
        if in_paper_section and '**' in line and ('：' in line or ':' in line):
            field_match = re.match(r'^\*\*([^*]+)\*\*[：:]\s*(.+)$', line)
            if field_match:
                field_name = field_match.group(1).strip()
                field_value = field_match.group(2).strip()
                
                # 映射字段名
                field_map = {
                    '作者': 'authors', 'Author': 'authors', 'author': 'authors',
                    '会议': 'conference', 'Conference': 'conference', 'conference': 'conference',
                    '年份': 'year', 'Year': 'year', 'year': 'year',
                    '摘要': 'abstract', 'Abstract': 'abstract', 'abstract': 'abstract',
                    '关键词': 'keywords', 'Keywords': 'keywords', 'keywords': 'keywords',
                }
                
                mapped_field = field_map.get(field_name, field_name.lower())
                if mapped_field == 'year':
                    year_match = re.search(r'(19|20)\d{2}', field_value)
                    if year_match:
                        current_paper['year'] = int(year_match.group(0))
                else:
                    current_paper[mapped_field] = field_value
                    if mapped_field == 'abstract':
                        current_field = 'abstract'
                    else:
                        current_field = None
            continue
        
        # 摘要续行
        if current_field == 'abstract' and current_paper:
            if 'abstract' in current_paper:
                current_paper['abstract'] += ' ' + line
            else:
                current_paper['abstract'] = line
    
    # 保存最后一篇论文
    if current_paper and 'title' in current_paper:
        papers.append(current_paper)
    
    return papers


def _validate_and_filter_papers(papers: List[Dict], conf_map: Dict) -> List[Dict]:
    """
    验证和过滤论文列表，并补充会议信息
    
    Args:
        papers: 原始论文列表
        conf_map: 会议ID到会议信息的映射
    
    Returns:
        验证后的论文列表
    """
    import re
    
    # 过滤无效的论文条目
    invalid_keywords = ['概述', '论文列表', '统计信息', '主题分布', 'Overview', 
                       'Paper List', 'Statistics', 'Topic Distribution', 'List', 'Summary']
    valid_papers = []
    
    for paper in papers:
        if not isinstance(paper, dict):
            continue
            
        title = paper.get('title', '').strip()
        # 跳过标题为空或包含无效关键词的条目
        if not title:
            continue
        
        # 检查标题是否包含无效关键词（不区分大小写）
        title_lower = title.lower()
        if any(keyword.lower() in title_lower for keyword in invalid_keywords):
            logger.debug(f"跳过无效论文条目: {title}")
            continue
        
        # 检查是否包含有效的论文信息（至少要有标题和作者或会议信息）
        if not (paper.get('authors') or paper.get('conference')):
            logger.debug(f"跳过信息不完整的论文条目: {title}（缺少 authors 或 conference）")
            continue
        
        valid_papers.append(paper)
    
    # 补充会议信息
    for paper in valid_papers:
        # 如果 conference_info_list 不为空，尝试匹配会议信息
        if conf_map:
            # 如果论文中有会议名称，尝试匹配到会议ID
            if 'conference' in paper and 'conference_id' not in paper:
                paper_conf = paper['conference']
                # 尝试从会议名称中提取年份
                year_match = re.search(r'(19|20)\d{2}', paper_conf)
                if year_match:
                    paper_year = int(year_match.group(0))
                    # 尝试匹配会议
                    for conf_id, conf_info in conf_map.items():
                        if (conf_info.get('year') == paper_year and 
                            (conf_info.get('short_name') in paper_conf or 
                             conf_info.get('full_name') in paper_conf)):
                            paper['conference_id'] = conf_id
                            paper['year'] = conf_info.get('year')
                            break
            
            # 如果已经有 conference_id，补充其他信息
            if 'conference_id' in paper:
                conf_id = paper['conference_id']
                if conf_id in conf_map:
                    info = conf_map[conf_id]
                    if 'conference' not in paper:
                        paper['conference'] = info.get('short_name') or info.get('full_name')
                    if 'year' not in paper:
                        paper['year'] = info.get('year')
        
        # 如果只有会议名称，尝试从名称中提取年份（无论 conf_map 是否为空）
        if 'conference' in paper and 'year' not in paper:
            year_match = re.search(r'(19|20)\d{2}', paper['conference'])
            if year_match:
                paper['year'] = int(year_match.group(0))
    
    return valid_papers

