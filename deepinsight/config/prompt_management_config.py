from typing import Optional

from pydantic import Field, BaseModel

from deepinsight.config.langfuse_config import LangfuseConfig


class PromptGroupConfig(BaseModel):
    label: str = Field(..., description="Prompt label")

class PromptManagementConfig(BaseModel):
    source: str = Field("local", description="Prompt source mode: local or remote")
    env: str = Field("dev",description="Environment for prompts: dev or prod")
    langfuse: Optional[LangfuseConfig] = Field(None, description="Langfuse config")
    groups: dict[str, PromptGroupConfig] = Field(..., description="Prompt group config")
    