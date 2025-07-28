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

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()


class DatabaseConfig:
    """数据库配置类"""

    def __init__(self, db_type: str = None, connection_string: str = None):
        """
        初始化数据库配置

        :param db_type: 数据库类型，如"postgresql"或"sqlite"
        :param connection_string: 数据库连接字符串
        """
        # 优先使用传入的参数，否则从环境变量获取，最后使用默认值
        self.db_type = db_type or os.getenv("DB_TYPE", "")

        if connection_string:
            self.connection_string = connection_string
        else:
            if self.db_type == "postgresql":
                # 从环境变量中获取数据库连接信息
                db_user = os.getenv('POSTGRES_USER', 'default_user')
                db_password = os.getenv('POSTGRES_PASSWORD', 'default_password')
                db_host = os.getenv('POSTGRES_HOST', 'localhost')
                db_port = os.getenv('POSTGRES_PORT', '5432')
                db_name = os.getenv('POSTGRES_DB', 'postgres')

                # 构建数据库连接字符串
                self.connection_string = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

            elif self.db_type == "sqlite":
                self.connection_string = os.getenv(
                    "SQLITE_CONN_STR",
                    "sqlite:///./chat_db.stores"
                )
            else:
                raise ValueError(f"不支持的数据库类型: {self.db_type}")


# 创建默认数据库配置
default_config = DatabaseConfig()

# 创建引擎
engine = create_engine(
    default_config.connection_string,
    echo=False,  # 设置为True可打印SQL语句，调试时使用
    connect_args={"check_same_thread": False} if default_config.db_type == "sqlite" else {}
)

# 创建会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# SQLAlchemy模型基类
# 所有数据模型都应继承此类，用于表结构定义
DatabaseModel = declarative_base()

# 数据库会话工厂
DatabaseSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_database_session():
    """
    获取数据库会话的生成器函数

    Yields:
        Session: 数据库会话对象

    Note:
        使用FastAPI等框架时，可作为依赖项注入
        会话会在使用后自动关闭
    """
    session = DatabaseSession()
    try:
        yield session
    finally:
        session.close()
