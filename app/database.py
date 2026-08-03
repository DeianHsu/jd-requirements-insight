"""该模块负责数据库地址解析、连接引擎创建、会话工厂创建和数据表初始化。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "jd_skill_insight.db"

# 已从当前代码删除、不得再出现于数据库的结构（旧派生数据的标志）。
_DELETED_TABLES = ("job_responsibilities", "requirement_relations")
_DELETED_COLUMNS = {
    "job_consolidations": ("hierarchy_status",),
    "requirement_mappings": ("status", "candidate_requirement_ids"),
}

# 当前主线依赖的关键表与关键列（现行归并 Schema）。
_REQUIRED_TABLES = (
    "job_descriptions",
    "job_extractions",
    "job_requirements",
    "job_consolidations",
    "canonical_requirements",
    "requirement_mappings",
)
_REQUIRED_COLUMNS = {
    "job_descriptions": {"id", "source_hash", "source_file", "raw_text"},
    "job_extractions": {
        "id",
        "job_id",
        "extractor_version",
        "prompt_version",
        "schema_version",
    },
    "job_requirements": {
        "id",
        "extraction_id",
        "raw_name",
        "importance",
        "proficiency",
        "group_logic",
        "evidence",
    },
    "job_consolidations": {
        "id",
        "scope_key",
        "consolidator_version",
        "input_fingerprint",
        "extractor_version",
        "occurrence_count",
    },
    "canonical_requirements": {
        "id",
        "consolidation_id",
        "canonical_requirement_id",
        "canonical_name",
        "source_requirement_ids",
        "rationale",
        "confidence",
    },
    "requirement_mappings": {
        "id",
        "consolidation_id",
        "requirement_id",
        "canonical_requirement_id",
        "rationale",
        "confidence",
    },
}


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """为每个SQLite连接启用外键约束，确保追溯和级联规则实际执行。"""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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


def assert_current_database_schema(engine: Engine) -> None:
    """检查数据库属于当前结构；发现缺失或旧结构时明确拒绝。

    只允许两种状态：完全空数据库（无任何项目业务表，可 create_all 创建
    当前结构），或已存在当前完整结构（关键表齐全且含关键列）。存在部分
    业务表、旧表、旧列、当前业务表缺少必需列时立即抛出清晰错误：

    - 不自动迁移、不自动补列、不自动删除；
    - 不在旧数据库上静默执行 create_all()；
    - 错误必须在主要业务查询或写入之前出现。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    violations: list[str] = []

    # 1. 旧结构拒绝。
    for table in _DELETED_TABLES:
        if table in existing_tables:
            violations.append(f"旧表 {table}")
    for table, columns in _DELETED_COLUMNS.items():
        if table not in existing_tables:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table)
        }
        for column in columns:
            if column in existing_columns:
                violations.append(f"旧列 {table}.{column}")

    # 2. 部分业务表拒绝（非空库必须包含全部当前必需表）。
    business_tables = existing_tables & set(_REQUIRED_TABLES)
    if business_tables and business_tables != set(_REQUIRED_TABLES):
        missing_tables = sorted(set(_REQUIRED_TABLES) - business_tables)
        violations.append(f"缺少必需表：{missing_tables}")

    # 3. 当前必需列缺失拒绝。
    for table in _REQUIRED_TABLES:
        if table not in existing_tables:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table)
        }
        missing_columns = sorted(_REQUIRED_COLUMNS[table] - existing_columns)
        if missing_columns:
            violations.append(f"表 {table} 缺少必需列：{missing_columns}")

    if violations:
        raise RuntimeError(
            "检测到非当前数据库结构：" + "；".join(violations) + "。\n"
            "当前代码只支持现行归并 Schema。\n"
            "请先备份 data/raw_jds/，删除旧派生数据库并重新生成。"
        )


def initialize_database(engine: Engine) -> None:
    """创建当前数据库结构；检测到缺失或旧派生结构时明确拒绝。

    完全空数据库由 `Base.metadata.create_all()` 创建当前结构；非空库
    必须已具备当前完整结构，否则报错要求用户备份重建。
    """
    assert_current_database_schema(engine)
    Base.metadata.create_all(engine)
