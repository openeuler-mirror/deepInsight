from typing import Optional

from pydantic import BaseModel, Field


class LangfuseConfig(BaseModel):
    public_key: Optional[str] = Field(None, description="Langfuse public key")
    secret_key: Optional[str] = Field(None, description="Langfuse secret key")
    host: Optional[str] = Field(None, description="Langfuse host address")