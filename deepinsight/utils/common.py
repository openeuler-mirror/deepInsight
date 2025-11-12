# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from typing import TypeVar, Callable, Optional

O = TypeVar("O")
T = TypeVar("T")


def safe_get(obj: O, accessor: Callable[[O], T], default: Optional[T] = None) -> Optional[T]:
    """
    Safely access a nested attribute or mapping value of a generic object, avoiding AttributeError.

    Args:
        obj: The object of type O to access.
        accessor: A lambda function taking O and returning T, e.g.,
            lambda c: c.scenarios.deep_research.final_report_model
        default: Value to return if access fails.

    Returns:
        The value of type T if access succeeds, else default.
    """
    try:
        return accessor(obj)
    except AttributeError:
        return default
    except Exception:
        return default