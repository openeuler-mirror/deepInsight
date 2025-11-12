from pydantic import BaseModel, Field


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

    conference_ppt_template_path: str | None = Field(
        default=None,
        description="PPT 模板路径（用于会议洞察报告生成）",
    )