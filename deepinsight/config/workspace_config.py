from typing import Optional, Annotated, Literal
from pydantic import BaseModel, Field, AnyHttpUrl


class WorkspaceConfig(BaseModel):
    """工作路径配置
    - work_root: 作为所有运行输出与存储的基础路径
    """

    work_root: str = Field(
        default="./data",
        description="Base working directory for outputs and storage",
    )

    chart_image_dir: str = Field(
        default="charts",
        description="Relative image save directory under work_root",
    )

    image_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for serving chart images",
    )

    image_path_mode: str = Field(
        default="relative",
        description="Path mode for image return: relative | base_url",
    )

    conference_ppt_template_path: Optional[str] = Field(
        default=None,
        description="PPT 模板路径（用于会议洞察报告生成）",
    )

    resource_base_uri: Literal["../../"] | Annotated[str, AnyHttpUrl] = "../../"
    """在 Markdown 中由 DeepInsight 生成的图片等超链接资源使用的 uri 前缀。
    
    对于本地运行模式，总是保持 ../../
    对于需要由 http 访问的场景，则应当开启 file_storage.remote_access 且与其设置或其他可访问方式保持一致。
    """
