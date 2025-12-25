# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
"""
会议相关的常量定义，统一管理 prompt_group、文件名和文件夹名。
"""


class ConferencePromptGroup:
    """会议相关的 prompt_group 常量"""
    OVERVIEW = "conf_gen_overview"
    SUBMISSION = "conf_gen_submission"
    KEYNOTES = "conf_gen_keynotes"
    TOPIC = "conf_gen_topic"
    BEST_PAPERS = "conf_gen_best_papers"


class ConferenceFileNames:
    """会议相关的文件名常量"""
    OVERVIEW_MD = "conference_overview.md"
    KEYNOTES_MD = "conference_keynotes.md"
    TOPIC_MD = "conference_topic.md"
    SUMMARY_MD = "conference_summary.md"
    SUBMISSION_MD = "conference_submission.md"


class ConferenceFolderNames:
    """会议相关的文件夹名常量"""
    BEST_PAPERS = "conference_best_papers"
    VALUE_MINING = "conference_value_mining"


def is_best_papers_group(prompt_group: str) -> bool:
    """判断是否是 best_papers 相关的 prompt_group"""
    return prompt_group == ConferencePromptGroup.BEST_PAPERS


def is_keynotes_group(prompt_group: str) -> bool:
    """判断是否是 keynotes 相关的 prompt_group"""
    return prompt_group == ConferencePromptGroup.KEYNOTES


def get_folder_name_for_prompt_group(prompt_group: str) -> str:
    """根据 prompt_group 获取对应的文件夹名
    
    Args:
        prompt_group: prompt_group 值，如 "conf_gen_best_papers", "conf_gen_keynotes"
    
    Returns:
        对应的文件夹名，如 "conference_best_papers"
    """
    if is_best_papers_group(prompt_group) or is_keynotes_group(prompt_group):
        return ConferenceFolderNames.BEST_PAPERS
    # 如果不是 best_papers 或 keynotes，返回 None 或抛出异常
    raise ValueError(f"Unknown prompt_group for folder: {prompt_group}")


def get_md_filename_for_prompt_group(prompt_group: str) -> str:
    """根据 prompt_group 获取对应的 markdown 文件名
    
    Args:
        prompt_group: prompt_group 值，如 "conf_gen_overview", "conf_gen_keynotes"
    
    Returns:
        对应的 markdown 文件名，如 "conference_overview.md", "conference_keynotes.md"
    """
    mapping = {
        ConferencePromptGroup.OVERVIEW: ConferenceFileNames.OVERVIEW_MD,
        ConferencePromptGroup.KEYNOTES: ConferenceFileNames.KEYNOTES_MD,
        ConferencePromptGroup.TOPIC: ConferenceFileNames.TOPIC_MD,
        ConferencePromptGroup.SUBMISSION: ConferenceFileNames.SUBMISSION_MD,
    }
    if prompt_group in mapping:
        return mapping[prompt_group]
    # best_papers 不生成单个 md 文件，而是生成文件夹
    if is_best_papers_group(prompt_group):
        raise ValueError(f"best_papers prompt_group does not generate a single md file, use get_folder_name_for_prompt_group instead")
    raise ValueError(f"Unknown prompt_group for md file: {prompt_group}")

