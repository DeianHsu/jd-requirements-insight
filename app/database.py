"""该模块负责数据库地址解析、连接引擎创建、会话工厂创建和数据表初始化。"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect
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
    """创建数据表并为旧SQLite数据库补充当前Schema所需字段。"""
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _migrate_sqlite_job_requirements_to_v2(engine)


def _migrate_sqlite_job_requirements_to_v2(engine: Engine) -> None:
    """以可重复执行的ALTER TABLE把旧岗位要求表升级到Schema V2。"""
    inspector = inspect(engine)
    if not inspector.has_table("job_requirements"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("job_requirements")
    }
    # SQLite的ADD COLUMN不会覆盖已有数据，静态SQL也避免把外部输入拼进DDL。
    column_definitions = {
        "group_id": "VARCHAR(100)",
        "group_logic": "VARCHAR(20) NOT NULL DEFAULT 'standalone'",
        "min_years": "FLOAT",
        "max_years": "FLOAT",
        "years_text": "VARCHAR(100)",
    }
    with engine.begin() as connection:
        for column_name, definition in column_definitions.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE job_requirements ADD COLUMN {column_name} {definition}"
                )
        # 旧库保留years_required列时，将已有最低年限一次性回填到V2字段。
        if "years_required" in existing_columns:
            connection.exec_driver_sql(
                "UPDATE job_requirements "
                "SET min_years = years_required "
                "WHERE min_years IS NULL AND years_required IS NOT NULL"
            )
        # create_all不会为已存在的旧表补索引，因此迁移阶段显式保证分组查询性能。
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_job_requirements_group_id "
            "ON job_requirements (group_id)"
        )
