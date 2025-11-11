import logging
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import DBAPIError
from contextlib import contextmanager
import threading

from deepinsight.config.database_config import DatabaseConfig


class Database:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_config: DatabaseConfig = None):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    # 兼容 DatabaseConfig 缺省字段，提供默认值
                    url = db_config.url
                    pool_size = getattr(db_config, 'pool_size', 5)
                    echo = getattr(db_config, 'echo', False)

                    engine = create_engine(
                        url,
                        future=True,
                        pool_pre_ping=True,
                        pool_recycle=3600,
                        pool_size=pool_size,
                        echo=echo,
                    )
                    SessionLocal = sessionmaker(
                        bind=engine,
                        autoflush=False,
                        autocommit=False,
                        future=True,
                    )
                    cls._instance = super().__new__(cls)
                    cls._instance.engine = engine
                    cls._instance.SessionLocal = SessionLocal
        return cls._instance

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except DBAPIError as e:
            session.rollback()
            logging.error(f"Execute session commit error {str(e)}")
            raise
        finally:
            session.close()
