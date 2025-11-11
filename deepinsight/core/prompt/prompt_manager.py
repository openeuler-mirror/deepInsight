import ast
import importlib
import inspect
import os
import pkgutil
import re
import logging
from typing import Dict, List, Optional
from tqdm import tqdm

from pydantic import BaseModel, Field
from langfuse import Langfuse
from langfuse.api import Prompt_Chat, ChatMessageWithPlaceholders_Chatmessage, NotFoundError, Prompt_Text
from langfuse.model import ChatMessageDict, ChatPromptClient
from langchain_core.prompts import ChatPromptTemplate

from deepinsight.config.prompt_management_config import PromptManagementConfig
from deepinsight.core.prompt import __path__ as prompts_pkg_path

valid_name_pattern = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def is_valid_prompt_name(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return False
    # 检查正则
    if not valid_name_pattern.match(name):
        return False
    # 可以加更多规则：比如名字不能全是下划线，不能包含多个连续下划线之类
    return True

class PromptMeta(BaseModel):
    name: str = Field(...)
    prompt: str = Field(...)
    group: str = Field(...)
    label: Optional[str] = Field(None)

def load_group_modules():
    group_modules = {}
    for module_info in pkgutil.iter_modules(prompts_pkg_path):
        module_name = module_info.name
        module = importlib.import_module(f"deepinsight.core.prompt.{module_name}")
        group_modules[module_name] = module
    return group_modules

_GROUP_MODULES = load_group_modules()

def get_group_file_path(group_name: str) -> str:
    if group_name not in _GROUP_MODULES:
        raise ValueError(f"Unknown group: {group_name}")
    module = _GROUP_MODULES[group_name]
    file_path = getattr(module, "__file__", None)
    if file_path is None:
        raise ValueError(f"Module {group_name} has no file path (__file__ missing)")
    return os.path.abspath(file_path)

def strip_quotes(s: str) -> str:
    """去掉字符串字面量外层引号（三引号、单引号都处理）"""
    if s.startswith(("r'''", 'r"""')) and s.endswith(("'''", '"""')):
        return s[4:-3]
    if s.startswith(("r'", 'r"')) and s.endswith(("'", '"')):
        return s[2:-1]
    if s.startswith(("'''", '"""')) and s.endswith(("'''", '"""')):
        return s[3:-3]
    if s.startswith(("'", '"')) and s.endswith(("'", '"')):
        return s[1:-1]
    return s

def get_prompts_by_group_from_source(group_name: str) -> list[PromptMeta]:
    file_path = get_group_file_path(group_name=group_name)
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=file_path)
    prompts = []

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and is_valid_prompt_name(target.id):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        # 获取源码片段
                        raw_literal = ast.get_source_segment(source, node.value)
                        if raw_literal:
                            string_value = strip_quotes(raw_literal)
                            prompts.append(
                                PromptMeta(
                                    name=target.id,
                                    group=group_name,
                                    prompt=string_value
                                )
                            )
    return prompts

def get_prompts_by_group_and_name(group_name: str, name: str) -> PromptMeta:
    if group_name not in _GROUP_MODULES:
        raise ValueError(f"Unknown prompt group: {group_name}")

    module = _GROUP_MODULES[group_name]
    for item_name, obj in inspect.getmembers(module):
        if isinstance(obj, str) and item_name == name:
            return PromptMeta(
                    name=item_name,
                    group=group_name,
                    prompt=obj
                )
    raise ValueError(f"Can not find {name} prompt in group {group_name}")

def get_prompts_by_group(group_name: str) -> List[PromptMeta]:
    if group_name not in _GROUP_MODULES:
        raise ValueError(f"Unknown prompt group: {group_name}")

    module = _GROUP_MODULES[group_name]
    prompts = []
    for name, obj in inspect.getmembers(module):
        if isinstance(obj, str) and is_valid_prompt_name(name):
            prompts.append(
                PromptMeta(
                    name=name,
                    group=group_name,
                    prompt=obj
                )
            )
    return prompts

def get_all_prompts() -> Dict[str, List[PromptMeta]]:
    result = {}
    for group, module in _GROUP_MODULES.items():
        result[group] = get_prompts_by_group(group)
    return result


def _format_prompt_name(name:str, group: str) -> str:
    return f"{group}_{name}"

class PromptManager:
    def __init__(self, config: PromptManagementConfig):
        self.config = config
        self.groups = config.groups
        if config.source not in {"local", "remote"}:
            raise ValueError("prompt_management.source must be either 'local' or 'remote'")

        self.source = config.source
        if self.source == "remote":
            if not config.langfuse:
                raise ValueError("Langfuse config must be provided when source=remote")
            try:
                self.langfuse_client = Langfuse(
                    public_key=config.langfuse.public_key,
                    secret_key=config.langfuse.secret_key,
                    host=config.langfuse.host,
                )
                if not self.langfuse_client.auth_check():
                    raise ConnectionError("Langfuse authentication failed")
            except Exception as e:
                logging.error(f"Failed to initialize Langfuse client: {e}")
                raise e
        else:
            for group_name in self.groups.keys():
                try:
                    _ = get_prompts_by_group_from_source(group_name)
                except Exception as e:
                    logging.error(
                        f"Failed to load local prompts for group '{group_name}'. "
                        f"Expected file core/prompts/{group_name}.py was not found or contains errors."
                    )
                    raise FileNotFoundError(
                        f"Failed to load local prompts for group '{group_name}'. "
                        f"Expected file core/prompts/{group_name}.py was not found or contains errors."
                    )

    def get_prompt(self, name: str, group: str) -> ChatPromptTemplate:
        if group not in self.groups:
            raise ValueError(f"Group '{group}' not configured in prompt_management.groups")

        label = self.groups[group].label
        unique_name = _format_prompt_name(name, group)
        if self.source == "local":
            try:
                local_meta = get_prompts_by_group_and_name(group, name)
            except Exception as e:
                raise Exception(f"Prompt '{group}' file get error: {e}")
            if not local_meta:
                raise FileNotFoundError(f"Prompt '{name}' not found in local group '{group}'")
            local_meta.label = label
            tpl = ChatPromptClient(
                prompt=Prompt_Chat(
                    name=unique_name,
                    version=1,
                    labels=[local_meta.label],
                    prompt=[
                        ChatMessageWithPlaceholders_Chatmessage(
                            role="system",
                            content=local_meta.prompt,
                        )
                    ],
                    tags=[],
                )
            ).get_langchain_prompt()
            return ChatPromptTemplate(tpl)
        
        elif self.source == "remote":
            try:
                remote = self.langfuse_client.get_prompt(unique_name, label=self._build_remote_label(label=label), type="chat")
                if not remote:
                    raise NotFoundError(f"Prompt '{name}' not found in remote group '{group}'")
                tpl = remote.get_langchain_prompt()
                return tpl
            except Exception as e:
                logging.error(f"Failed to fetch prompt '{name}' from Langfuse: {e}")
                raise e
    
    def sync_remote_prompts_to_local(self, output_dir="prompts"):
        """
        Sync prompts from Langfuse for each group in self.groups.
        Fetch all prompts whose name starts with '{group}_'.
        Write them into local .py files, one file per group.
        """
        if self.source != "remote":
            raise RuntimeError("sync_remote_prompts_to_local is only available when source=remote")

        os.makedirs(output_dir, exist_ok=True)

        print("Syncing prompts from Langfuse for configured groups...")

        all_prompts = []
        page, limit = 1, 100
        while True:
            overview = self.langfuse_client.api.prompts.list(page=page, limit=limit)
            if not overview.data:
                break

            all_prompts.extend(overview.data)

            if overview.meta.page >= overview.meta.total_pages:
                break
            page += 1
        print(f"Fetched total {len(all_prompts)} remote prompt metas.")
        grouped_prompts: dict[str, list[tuple[str, object]]] = {g: [] for g in self.groups.keys()}
        for meta in tqdm(all_prompts, desc="Filtering prompts", unit="prompt"):
            for group_name, group_cfg in self.groups.items():
                if meta.name.startswith(f"{group_name}_") and self._build_remote_label(label=group_cfg.label) in (meta.labels or []):
                    try:
                        detail = self.langfuse_client.api.prompts.get(meta.name, label=self._build_remote_label(label=group_cfg.label))
                        grouped_prompts[group_name].append((meta.name, detail))
                        tqdm.write(f"Matched prompt {meta.name} for group {group_name}")
                    except Exception as e:
                        tqdm.write(f"Error fetching details for {meta.name}: {e}")

        for group_name, group_cfg in tqdm(self.groups.items(), desc="Writing groups", unit="group"):
            file_path = os.path.join(output_dir, f"{group_name}.py")
            prompts_for_group = grouped_prompts.get(group_name, [])

            if not prompts_for_group:
                tqdm.write(f"No prompts synced for group {group_name}")
                continue

            # sort by variable name
            sorted_prompts = []
            for full_name, prompt_obj in prompts_for_group:
                if full_name.startswith(group_name):
                    var_name = full_name[len(group_name):]
                    if var_name.startswith("_"):
                        var_name = var_name[1:]
                else:
                    var_name = full_name
                sorted_prompts.append((var_name, prompt_obj))

            sorted_prompts.sort(key=lambda x: x[0])  # sort by variable name

            with open(file_path, "w", encoding="utf-8") as f:
                for var_name, prompt_obj in sorted_prompts:
                    # extract content
                    if isinstance(prompt_obj, Prompt_Chat):
                        try:
                            content = "\n".join([m.content for m in prompt_obj.prompt])
                        except Exception:
                            content = str(prompt_obj.prompt)
                    elif isinstance(prompt_obj, Prompt_Text):
                        content = prompt_obj.prompt
                    else:
                        raise ValueError(f"Unsupported prompt type for {var_name}")

                    f.write(f"{var_name} = r\"\"\"\n{content.strip()}\n\"\"\"\n\n")

            tqdm.write(f"Synced {len(sorted_prompts)} prompts to {file_path}")
    
    def sync_local_prompts_to_remote(self, prompt_dir="prompts"):
        for group_name, group_cfg in tqdm(self.groups.items(), desc="Groups", unit="group"):
            local_prompts = get_prompts_by_group_from_source(group_name)
            for prompt_meta in local_prompts:
                prompt_meta.label = group_cfg.label
                self.langfuse_client.create_prompt(
                    name=_format_prompt_name(prompt_meta.name, prompt_meta.group),
                    prompt=[
                        ChatMessageDict(
                            role="system",
                            content=prompt_meta.prompt,
                        )
                    ],
                    type="chat",
                    labels=[self._build_remote_label(prompt_meta.label)],
                )
                tqdm.write(f"Created or update prompt {prompt_meta.name} in group {group_name}")
                
    def _build_remote_label(self, label: str) -> str:
        return f"{self.config.env}-{label}"
    