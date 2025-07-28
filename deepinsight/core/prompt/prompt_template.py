# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from enum import Enum
from typing import Dict, Optional, Union, List

from pydantic import BaseModel, field_validator

from deepinsight.core.prompt import report
from deepinsight.core.prompt import plan, research


class PromptStage(str, Enum):
    """Enum defining all possible prompt usage stages"""
    PLAN_SYSTEM = "plan_system"
    PLAN_USER = "plan_user"
    EXECUTE_SYSTEM = "execute_system"
    EXECUTE_USER = "execute_user"
    REVIEW_SYSTEM = "review_system"
    REVIEW_USER = "review_user"
    RESEARCH_ROLE_PLAYING_USER_SYSTEM = "research_role_playing_user_system"
    RESEARCH_ROLE_PLAYING_USER_USER = "research_role_playing_user_user"
    RESEARCH_ROLE_PLAYING_ASSISTANT_SYSTEM = "research_role_playing_assistant_system"
    REPORT_PLAN_SYSTEM = "report_plan_system"
    REPORT_PLAN_USER = "report_plan_user"
    REPORT_WRITE_SYSTEM = "report_write_system"
    REPORT_WRITE_USER = "report_write_user"
    PLAN_START_TIPS = "plan_start_tips"
    RESEARCH_START_TIPS = "research_start_tips"
    REPORT_PLAN_TIPS = "report_plan_tips"
    REPORT_WRITE_TIPS = "report_write_tips"
    ERROR_RECOVERY = "error_recovery"
    CUSTOM = "custom"


DEFAULT_PROMPT_MAP = {
    PromptStage.PLAN_SYSTEM: plan.DEFAULT_SYSTEM_PROMPT,
    PromptStage.PLAN_USER: plan.DEFAULT_USER_PROMPT,
    PromptStage.RESEARCH_ROLE_PLAYING_USER_SYSTEM: research.DEFAULT_ROLE_PLAYING_USER_SYSTEM,
    PromptStage.RESEARCH_ROLE_PLAYING_USER_USER: research.DEFAULT_ROLE_PLAYING_USER_USER,
    PromptStage.RESEARCH_ROLE_PLAYING_ASSISTANT_SYSTEM: research.DEFAULT_ROLE_PLAYING_ASSISTANT_SYSTEM,
    PromptStage.REPORT_PLAN_SYSTEM: report.DEFAULT_REPORT_PLAN_SYSTEM,
    PromptStage.REPORT_PLAN_USER: report.DEFAULT_REPORT_PLAN_USER,
    PromptStage.REPORT_WRITE_SYSTEM: report.DEFAULT_REPORT_WRITE_SYSTEM,
    PromptStage.REPORT_WRITE_USER: report.DEFAULT_REPORT_WRITE_USER,
}


class PromptTemplate(BaseModel):
    """
    Flexible prompt template system using {} formatting style.
    Manages prompt templates for different stages of research workflow.
    """

    template_dict: Dict[PromptStage, str]
    """
    Core storage for stage-specific prompt templates.

    Structure:
        Key: PromptStage enum value
        Value: String template with {variables} using str.format() syntax

    Features:
        - Predefined stages ensure consistency
        - Python str.format() compatible variables
        - Stage-specific specialization

    Example:
        {
            PromptStage.PLAN_SYSTEM: "You are a {role} specializing in {domain}...",
            PromptStage.EXECUTE_USER: "Execute this task: {task_description}"
        }
    """

    custom_variables: Dict[str, Union[str, int, float]] = {}
    """
    Global variables available for all templates.

    Usage:
        - Shared across multiple prompt generations
        - Automatically injected into templates

    Example:
        {"project": "DL Optimization", "max_length": 500}
    """

    strict_validation: bool = True
    """
    Safety control for template variables.

    When True:
        - Raises error if template uses undefined variables
    When False:
        - Silently ignores missing variables (replaces with empty string)
    """

    class Config:
        """Pydantic configuration"""
        use_enum_values = True
        extra = "forbid"

    @field_validator('template_dict')
    def validate_template_syntax(cls, v):
        """Verify all templates contain valid {} formatting syntax"""
        for stage, template in v.items():
            try:
                # Test formatting with empty values
                template.format(**{k: '' for k in cls._extract_placeholders(template)})
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid template syntax in {stage}: {str(e)}")
        return v

    @staticmethod
    def _extract_placeholders(template: str) -> List[str]:
        """Extract all {placeholder} names from a template string"""
        from string import Formatter
        return [fn for _, fn, _, _ in Formatter().parse(template) if fn is not None]

    def get_prompt(
            self,
            stage: Union[PromptStage, str],
            variables: Optional[Dict] = None,
            allow_partial: bool = False
    ) -> str:
        """
        Retrieve and render a prompt template for the specified stage.

        Args:
            stage: Target prompt stage (enum or string)
            variables: Stage-specific variables (merged with custom_variables)
            allow_partial: Permit missing variables when False

        Returns:
            Rendered prompt string

        Raises:
            ValueError: On invalid stage or missing variables (strict mode)
        """
        # Convert string stages to enum
        if isinstance(stage, str):
            stage = PromptStage(stage.lower())

        # Merge all variables
        merged_vars = {**self.custom_variables, **(variables or {})}

        # Get template
        template_str = self.template_dict.get(stage)
        if template_str is None:
            raise ValueError(f"No template defined for stage: {stage}")

        # Render template
        try:
            return template_str.format(**merged_vars)
        except KeyError as e:
            if allow_partial or not self.strict_validation:
                return template_str.format(
                    **{k: merged_vars.get(k, '') for k in self._extract_placeholders(template_str)})
            raise ValueError(f"Missing variables for {stage}: {str(e)}")

    def add_template(
            self,
            stage: Union[PromptStage, str],
            template: str,
            overwrite: bool = False,
            validate: bool = True
    ) -> None:
        """
        Register a new template for a specific stage.

        Args:
            stage: Target stage identifier
            template: String template with {variables}
            overwrite: Allow replacing existing templates
            validate: Check template syntax before adding

        Raises:
            ValueError: On invalid template or protected stage
        """
        if isinstance(stage, str):
            stage = PromptStage(stage.lower())

        if stage in self.template_dict and not overwrite:
            raise ValueError(f"Template already exists for {stage}")

        if validate:
            try:
                placeholders = self._extract_placeholders(template)
                template.format(**{k: '' for k in placeholders})
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid template syntax: {str(e)}")

        self.template_dict[stage] = template

    @classmethod
    def create_default(cls) -> 'PromptTemplate':
        """Factory method with recommended default templates"""
        default_templates = {
        }
        for stage, template in DEFAULT_PROMPT_MAP.items():
            default_templates[stage] = template
        return cls(template_dict=default_templates)

    def get_placeholders(self, stage: Union[PromptStage, str]) -> List[str]:
        """
        Get all required variable names for a specific template stage.

        Args:
            stage: Target stage identifier

        Returns:
            List of required placeholder names
        """
        if isinstance(stage, str):
            stage = PromptStage(stage.lower())
        template = self.template_dict.get(stage)
        if not template:
            return []
        return self._extract_placeholders(template)


GLOBAL_DEFAULT_PROMPT_REPOSITORY = PromptTemplate.create_default()
