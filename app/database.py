"""该模块负责数据库地址解析、连接引擎创建、会话工厂创建和数据表初始化。"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "jd_skill_insight.db"


def default_database_url() -> str | URL:
    """优先返回环境变量中的数据库地址，否则使用项目内的默认SQLite文件。"""
    # 环境变量允许测试或部署环境替换数据库，而不需要修改业务代码。
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url
    # 使用URL对象构造Windows绝对路径，避免盘符和空格被字符串URL错误解析。
    return URL.create("sqlite", database=str(DEFAULT_DATABASE_PATH))


def create_database_engine(database_url: str | URL | None = None) -> Engine:
    """根据数据库地址创建SQLAlchemy Engine，并确保SQLite父目录存在。"""
    url = database_url or default_database_url()
    parsed_url = make_url(url)
    # SQLite不会自动创建父目录，因此在首次连接前主动准备存储目录。
    if parsed_url.drivername == "sqlite" and parsed_url.database != ":memory:":
        if parsed_url.database:
            Path(parsed_url.database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建可复用的Session工厂，让每次业务操作使用独立的短生命周期会话。"""
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    """根据ORM模型创建尚不存在的数据表，重复执行不会删除已有数据。"""
    Base.metadata.create_all(engine)
