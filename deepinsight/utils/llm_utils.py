import logging
from typing import Dict, List, Tuple, Callable, Any, Optional

from pydantic import BaseModel, ValidationError, SecretStr

# LangChain imports
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from deepinsight.config.config import Config
from deepinsight.config.llm_config import LLMConfig
from deepinsight.service.schemas.research import ArgOptionsGeneric
from lightrag.llm.openai import openai_complete_if_cache


def _normalize_settings_kwargs(setting: Any) -> Dict[str, Any]:
    """
    Normalize LLM settings to kwargs dict.
    - Supports Pydantic v2 models (model_dump), Pydantic v1 models (.dict()), and plain dicts.
    - Filters out None values.
    """
    if setting is None:
        return {}

    try:
        if isinstance(setting, BaseModel):
            # Prefer v2 model_dump if available
            try:
                return setting.model_dump(exclude_none=True)
            except Exception:
                # Fallback for pydantic v1
                return setting.model_dump(exclude_none=True)
    except Exception:
        # If BaseModel import/type check fails for any reason, continue
        pass

    if isinstance(setting, dict):
        return {k: v for k, v in setting.items() if v is not None}

    # Unknown type, ignore
    return {}


def init_langchain_models_from_llm_config(
    llm_config: List[LLMConfig | ArgOptionsGeneric[LLMConfig]],
) -> Tuple[Dict[str, BaseChatModel], BaseChatModel]:
    """
    初始化 LangChain 所需的聊天模型集合，并返回默认模型。
    - 输入为 LLMConfig 列表（来自 config.yaml）
    - 优先使用 LangChain 的 init_chat_model，根据供应商自动选择后端
    - 失败时回退到 ChatOpenAI（支持 OpenAI 兼容接口）
    - 返回：{"type:model": BaseChatModel}, default_model
    """
    models: Dict[str, BaseChatModel] = {}
    default_model: Optional[BaseChatModel] = None

    def _extract_fields(item: Any) -> tuple[str, str, Optional[str], Optional[str], Any]:
        """
        Extract (provider, model, base_url, api_key, setting) from either LLMConfig
        or ArgOptionsGeneric[LLMConfig]-like objects.
        """
        # ArgOptionsGeneric
        if hasattr(item, "params") and hasattr(item, "type"):
            provider = getattr(item, "type")
            params = getattr(item, "params")
            model = getattr(params, "model", None)
            base_url = getattr(params, "base_url", None)
            api_key = getattr(params, "api_key", None)
            setting = getattr(params, "setting", None)
            return provider, model, base_url, api_key, setting
        # Plain LLMConfig
        provider = getattr(item, "type", None)
        model = getattr(item, "model", None)
        base_url = getattr(item, "base_url", None)
        api_key = getattr(item, "api_key", None)
        setting = getattr(item, "setting", None)
        return provider, model, base_url, api_key, setting

    for each in llm_config:
        provider, model_name, base_url, api_key, setting = _extract_fields(each)
        if not provider or not model_name:
            logging.error(f"Invalid LLM item, missing provider/model: {each}")
            continue
        key = f"{provider}:{model_name}"
        settings_kwargs = _normalize_settings_kwargs(setting)
        settings_kwargs.setdefault("timeout", 300)
        try:
            model = init_chat_model(
                model_provider=provider,
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                **settings_kwargs,
            )
            models[key] = model
            if not default_model:
                default_model = model
        except ValidationError as e:
            logging.error(f"Init chat model error: {e}")
            raise e
        except ValueError as e:
            logging.warning(
                f"Cannot directly init model {key} via init_chat_model, falling back to ChatOpenAI. Error: {e}"
            )
            model = ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                **settings_kwargs,
            )
            models[key] = model
            if not default_model:
                default_model = model
        except Exception as e:
            logging.error(f"Init model {key} error: {e}")
            continue

    if not default_model:
        raise ValueError(
            "Failed to initialize LLM. Please check the provided LLM configuration parameters",
        )
    return models, default_model


def _extract_model_credentials(model: BaseChatModel) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """从 LangChain 模型实例中尽可能提取 (model_name, api_key, base_url)。
    - 优先支持 ChatOpenAI 的属性访问
    - 对其他模型尝试常见字段名的回退
    - 兼容 pydantic SecretStr，解包为原始字符串
    """
    def _to_plain_str(value: Any) -> Optional[str]:
        """将可能的 SecretStr 值解包为原始字符串。"""
        try:
            if isinstance(value, SecretStr):
                return value.get_secret_value()
            # 兼容 SecretStr-like 对象
            if hasattr(value, "get_secret_value") and callable(getattr(value, "get_secret_value")):
                return value.get_secret_value()
        except Exception:
            pass
        return value

    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    # ChatOpenAI 的常见字段
    if isinstance(model, ChatOpenAI):
        try:
            name = getattr(model, "model", None)
        except Exception:
            pass
        try:
            api_key = getattr(model, "api_key", None)
        except Exception:
            pass
        try:
            base_url = getattr(model, "base_url", None)
        except Exception:
            pass
        return _to_plain_str(name), _to_plain_str(api_key), _to_plain_str(base_url)

    # 其他模型的通用尝试
    for attr in ("model", "model_name", "deployment_name"):
        if name is None:
            name = getattr(model, attr, None)
    for attr in ("api_key", "openai_api_key", "token"):
        if api_key is None:
            api_key = getattr(model, attr, None)
    for attr in ("base_url", "openai_api_base", "endpoint"):
        if base_url is None:
            base_url = getattr(model, attr, None)

    return _to_plain_str(name), _to_plain_str(api_key), _to_plain_str(base_url)


def init_lightrag_llm_model_func(cfg: Config) -> Callable[..., Any]:
    """
    初始化供 LightRAG 使用的 llm_model_func 闭包。
    - 不读取任何环境变量
    - 先调用 init_langchain_models_from_llm_config(cfg.llms) 初始化模型集合
    - 从默认模型实例中提取 model/api_key/base_url 作为调用 openai_complete_if_cache 的参数
    - 若某些字段无法从实例中提取，则回退到 Config.llms[0]
    - 将 setting 作为默认生成参数，并允许运行时 **kwargs 覆盖
    """
    models, default_model = init_langchain_models_from_llm_config(cfg.llms)

    model_name, api_key, base_url = _extract_model_credentials(default_model)

    # 回退到配置
    llm_cfg = cfg.llms[0] if (hasattr(cfg, "llms") and cfg.llms) else None
    if model_name is None:
        model_name = llm_cfg.model if llm_cfg else None
    if api_key is None:
        api_key = llm_cfg.api_key if llm_cfg else None
    if base_url is None:
        base_url = llm_cfg.base_url if llm_cfg else None

    cfg_kwargs = _normalize_settings_kwargs(llm_cfg.setting) if llm_cfg else {}

    if not model_name:
        raise ValueError(
            "LLM model not configured. Please provide a valid model in config.yaml.",
        )

    def llm_model_func(prompt, system_prompt=None, history_messages=[], **kwargs):
        merged_kwargs = {**cfg_kwargs, **kwargs}
        merged_kwargs.setdefault("timeout", 300)
        
        # Filter out internal LightRAG parameters that aren't supported by OpenAI SDK
        # These are internal parameters used by LightRAG for priority/routing
        unsupported_params = {'_priority', '_source', '_task_id', '_retry_count'}
        filtered_kwargs = {k: v for k, v in merged_kwargs.items() if k not in unsupported_params}

        return openai_complete_if_cache(
            model_name,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key=api_key,
            base_url=base_url,
            **filtered_kwargs,
        )

    return llm_model_func
