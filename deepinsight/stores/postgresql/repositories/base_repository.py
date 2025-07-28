# Copyright (c) 2025 Huawei Technologies Co. Ltd.
# deepinsight is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
from sqlalchemy.orm import Session
from typing import Generic, TypeVar, List, Optional, Type

# 泛型类型变量
ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    """基础数据访问类，提供通用的CRUD操作"""

    def __init__(self, db: Session, model: Type[ModelType]):
        """
        初始化基础仓库
        
        :param db: 数据库会话
        :param model: 数据模型类
        """
        self.db = db
        self.model = model

    def create(self, obj: ModelType) -> ModelType:
        """
        创建新记录
        
        :param obj: 要创建的对象
        :return: 创建后的对象
        """
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def get_by_id(self, id: str) -> Optional[ModelType]:
        """
        根据ID获取记录
        
        :param id: 记录ID
        :return: 找到的对象或None
        """
        return self.db.query(self.model).filter(self.model.id == id).first()

    def get_all(self, skip: int = 0, limit: int = 100) -> List[ModelType]:
        """
        获取所有记录
        
        :param skip: 跳过的记录数
        :param limit: 最大返回记录数
        :return: 记录列表
        """
        return self.db.query(self.model).offset(skip).limit(limit).all()

    def update(self, obj: ModelType) -> ModelType:
        """
        更新记录
        
        :param obj: 要更新的对象
        :return: 更新后的对象
        """
        self.db.merge(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def delete(self, obj: ModelType) -> None:
        """
        删除记录
        
        :param obj: 要删除的对象
        """
        self.db.delete(obj)
        self.db.commit()
