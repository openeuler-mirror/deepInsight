from datetime import datetime

from sqlalchemy import Column, Integer, String, TIMESTAMP, Text, JSON, Index

from deepinsight.databases.models import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    kb_id = Column(Integer, primary_key=True, autoincrement=True)
    owner_type = Column(String(50), nullable=False)  # 固定为"conference"，也支持未来扩展
    owner_id = Column(Integer, nullable=True)  # 会议ID，生成成功后绑定
    root_dir = Column(String(255), nullable=False)
    index_dir = Column(String(255))
    parser = Column(String(50))
    parse_method = Column(String(20))
    embed_model = Column(String(100))
    status = Column(String(20), nullable=False)  # init|processing|ready|failed
    doc_count = Column(Integer, nullable=False, default=0)
    last_built_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"
    __table_args__ = (
        # 使用命名约定自动生成名称（ix_<table>_<columns>），不显式给名字
        Index(None, 'kb_id'),
    )
    
    doc_id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(Integer, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255))
    md5 = Column(String(64))
    parse_status = Column(String(20))  # pending|processing|parsed|failed
    chunks_count = Column(Integer, nullable=False, default=0)
    paper_meta = Column(JSON)
    failed_reason = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)
