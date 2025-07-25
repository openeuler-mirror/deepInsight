# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
import os


def check_env_vars(env_vars: list[str]):
    """
    Verify that required environment variables are set.
    Raises ValueError if any of the required environment variables are not set.

    Args:
        env_vars (list[str]): List of environment variable names to check.

    Raises:
        ValueError: If any of the required environment variables are missing.

    Examples:
        >>> check_env_vars(["PATH", "HOME"])  # Check PATH and HOME environment variables
        >>> check_env_vars(["API_KEY", "DB_URL"])  # Check API_KEY and DB_URL environment variables
    """
    missing_vars = [var for var in env_vars if not os.getenv(var)]
    if missing_vars:
        error_msg = f"缺少以下一个或多个必需的环境变量: {', '.join(missing_vars)}"
        raise ValueError(error_msg)