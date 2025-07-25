# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from typing import Optional, Dict, Union

from camel.models import BaseModelBackend, ModelFactory
from camel.types import ModelPlatformType
from pydantic import BaseModel, ConfigDict


class ModelConfig(BaseModel):
    """
    Complete configuration structure for language model deployments.
    Combines platform, model specification, and connection parameters.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_platform: Union[ModelPlatformType, str]
    """
    Target platform or service provider for the model.

    Types:
        ModelPlatformType | str:
        ▪ Predefined platforms via ModelPlatformType enum

        ▪ Custom platform names as strings


    Examples:
        ModelPlatformType.OPENAI
        "azure_openai"
        "huggingface_endpoint"
    """

    model_type: Union[str]
    """
    Specification of the model variant/version.

    Types:
        str: Can refer camel's ModelType

    Examples:
        ModelType.GPT_4
        UnifiedModelType.PRECISE  
        "custom-llm-v2.3"
    """

    model_config_dict: Optional[Dict] = None
    """
    Extended model parameters and generation settings.

    Types:
        Optional[Dict]:
        ▪ None: Use default parameters

        ▪ Dict: Custom configuration


    Common Keys:
        ▪ "temperature": Creativity control (0-2)

        ▪ "max_tokens": Response length limit

        ▪ "top_p": Nucleus sampling threshold


    Example:
        {"temperature": 0.7, "stop_sequences": ["\n"]}
    """

    api_key: Optional[str] = None
    """
    Authentication credential for API access.

    Types:
        Optional[str]:
        ▪ None: Attempt environment variable lookup

        ▪ str: Explicit credential string


    Security:
        ▪ Marked optional but required for most cloud platforms

        ▪ Recommended to inject via environment variables

    """

    base_url: Optional[str] = None
    """
    Custom API endpoint configuration.

    Types:
        Optional[str]:
        ▪ None: Use provider's default endpoint  

        ▪ str: Custom base URL


    Use Cases:
        ▪ Self-hosted model deployments

        ▪ API proxy services

        ▪ Local testing endpoints

    """

    def to_model_backend(self) -> BaseModelBackend:
        """
        Convert the current configuration into a ready-to-use model backend instance.

        This method acts as a bridge between configuration storage and operational backend,
        transforming the declarative ModelConfig into an executable model interface.

        Returns:
            BaseModelBackend:
                An initialized model backend instance ready for inference tasks.
                The concrete type depends on the specified model platform and type.

        Process Flow:
            1. Delegates to ModelFactory with all necessary parameters
            2. Factory selects appropriate backend implementation
            3. Initializes backend with provided configuration
            4. Returns ready-to-use backend instance

        Example Usage:
            >>> config = ModelConfig(...)
            >>> model = config.to_model_backend()
        """
        return ModelFactory.create(
            model_platform=self.model_platform,
            model_type=self.model_type,
            model_config_dict=self.model_config_dict,
            api_key=self.api_key,
            url=self.base_url,
        )