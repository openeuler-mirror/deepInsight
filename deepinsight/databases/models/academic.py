from datetime import datetime

from sqlalchemy import Column, Integer, String, TIMESTAMP, Date, JSON, Text, Boolean
from sqlalchemy import UniqueConstraint

from deepinsight.databases.models import Base


class Author(Base):
    __tablename__ = "author"

    author_id = Column(Integer, primary_key=True, autoincrement=True)
    conference_id = Column(Integer, nullable=False)
    author_name = Column(String(100), nullable=False)
    email = Column(String(255))
    affiliation = Column(String(255))
    affiliation_country = Column(String(100))
    affiliation_city = Column(String(100))
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class Conference(Base):
    __tablename__ = "conference"
    __table_args__ = (
        # 依赖全局命名规范自动命名 uq_conference_short_name_year
        UniqueConstraint("short_name", "year"),
    )

    conference_id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(255), nullable=False)
    short_name = Column(String(50))
    year = Column(Integer, nullable=False)
    location = Column(String(100))
    start_date = Column(Date)
    end_date = Column(Date)
    website = Column(String(255))
    topics = Column(JSON)
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class Paper(Base):
    __tablename__ = "paper"

    paper_id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    conference_id = Column(Integer, nullable=False)  # 直接存储ID，不使用ForeignKey
    publication_year = Column(Integer)
    abstract = Column(Text)
    keywords = Column(String(255))
    author_ids = Column(String(500))  # 存储作者ID列表，如 "1,3,5"
    reference_ids = Column(String(500))  # 存储参考文献ID列表
    topic = Column(String(100), nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class PaperAuthorRelation(Base):
    __tablename__ = "paper_author_relation"

    relation_id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, nullable=False)  # 直接存储ID
    author_id = Column(Integer, nullable=False)  # 直接存储ID
    author_order = Column(Integer, nullable=False)
    is_corresponding = Column(Boolean, default=False, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.now)
