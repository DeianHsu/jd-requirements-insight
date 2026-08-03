"""该模块负责数据库地址解析、连接引擎创建、会话工厂创建和数据表初始化。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "jd_skill_insight.db"


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


def initialize_database(engine: Engine) -> None:
    """创建数据表并为旧SQLite数据库补充当前数据库结构所需字段。"""
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _migrate_sqlite_job_requirements_to_v2(engine)
        _migrate_sqlite_consolidations_to_input_identity(engine)
        _ensure_sqlite_consolidation_relation_index(engine)
        _migrate_sqlite_consolidations_hierarchy_status(engine)
        _rebuild_sqlite_consolidation_relations(engine)


def _migrate_sqlite_job_requirements_to_v2(engine: Engine) -> None:
    """以可重复执行的ALTER TABLE把旧岗位要求表升级到数据库结构V2。"""
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


def _legacy_consolidation_identity(
    consolidation_id: int,
    scope_key: str,
    consolidator_version: str,
    requirement_ids: list[int],
) -> str:
    """为无法还原完整历史输入的旧批次生成稳定且明确隔离的指纹。"""
    payload = {
        "legacy_consolidation_id": consolidation_id,
        "scope_key": scope_key,
        "consolidator_version": consolidator_version,
        "requirement_ids": requirement_ids,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _migrate_sqlite_consolidations_to_input_identity(engine: Engine) -> None:
    """重建旧归并主表，保留历史批次并把输入指纹纳入唯一身份。"""
    inspector = inspect(engine)
    if not inspector.has_table("job_consolidations"):
        return
    existing_columns = {
        column["name"] for column in inspector.get_columns("job_consolidations")
    }
    required_columns = {
        "input_fingerprint",
        "extractor_version",
        "selected_job_ids",
        "extraction_ids",
    }
    if required_columns <= existing_columns:
        return

    with engine.connect() as connection:
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(f"数据库存在外键异常，拒绝迁移归并批次：{violations}")

    has_mappings = inspector.has_table("requirement_mappings")
    raw_connection = engine.raw_connection()
    cursor = raw_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            "CREATE TABLE job_consolidations_new ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "scope_key VARCHAR(255) NOT NULL, "
            "consolidator_version VARCHAR(255) NOT NULL, "
            "input_fingerprint VARCHAR(64) NOT NULL, "
            "extractor_version VARCHAR(255) NOT NULL, "
            "selected_job_ids JSON NOT NULL, "
            "extraction_ids JSON NOT NULL, "
            "model_name VARCHAR(255) NOT NULL, "
            "prompt_version VARCHAR(50) NOT NULL, "
            "schema_version VARCHAR(50) NOT NULL, "
            "occurrence_count INTEGER NOT NULL, "
            "raw_response JSON NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "CONSTRAINT uq_scope_consolidator_input UNIQUE "
            "(scope_key, consolidator_version, input_fingerprint)"
            ")"
        )
        cursor.execute(
            "SELECT id, scope_key, consolidator_version, model_name, "
            "prompt_version, schema_version, occurrence_count, raw_response, created_at "
            "FROM job_consolidations ORDER BY id"
        )
        old_rows = cursor.fetchall()
        for row in old_rows:
            consolidation_id = row[0]
            source_rows = []
            if has_mappings:
                cursor.execute(
                    "SELECT rm.requirement_id, je.id, je.job_id, je.extractor_version "
                    "FROM requirement_mappings AS rm "
                    "JOIN job_requirements AS jr ON jr.id = rm.requirement_id "
                    "JOIN job_extractions AS je ON je.id = jr.extraction_id "
                    "WHERE rm.consolidation_id = ? ORDER BY rm.requirement_id",
                    (consolidation_id,),
                )
                source_rows = cursor.fetchall()
            requirement_ids = [source_row[0] for source_row in source_rows]
            extraction_ids = sorted({source_row[1] for source_row in source_rows})
            selected_job_ids = sorted({source_row[2] for source_row in source_rows})
            extractor_versions = sorted({source_row[3] for source_row in source_rows})
            if len(extractor_versions) == 1:
                extractor_version = extractor_versions[0]
            elif extractor_versions:
                extractor_version = "legacy:mixed"
            else:
                extractor_version = "legacy:unknown"
            input_fingerprint = _legacy_consolidation_identity(
                consolidation_id,
                row[1],
                row[2],
                requirement_ids,
            )
            cursor.execute(
                "INSERT INTO job_consolidations_new ("
                "id, scope_key, consolidator_version, input_fingerprint, "
                "extractor_version, selected_job_ids, extraction_ids, model_name, "
                "prompt_version, schema_version, occurrence_count, raw_response, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    consolidation_id,
                    row[1],
                    row[2],
                    input_fingerprint,
                    extractor_version,
                    json.dumps(selected_job_ids),
                    json.dumps(extraction_ids),
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                ),
            )
        cursor.execute("DROP TABLE job_consolidations")
        cursor.execute(
            "ALTER TABLE job_consolidations_new RENAME TO job_consolidations"
        )
        cursor.execute(
            "CREATE INDEX ix_job_consolidations_scope_key "
            "ON job_consolidations (scope_key)"
        )
        raw_connection.commit()
        cursor.execute("PRAGMA foreign_keys=ON")
        violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"归并批次迁移后外键校验失败：{violations}")
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        cursor.close()
        raw_connection.close()


def _ensure_sqlite_consolidation_relation_index(engine: Engine) -> None:
    """为旧SQLite关系表补充批次内关系三元组唯一索引。"""
    inspector = inspect(engine)
    if not inspector.has_table("requirement_relations"):
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_consolidation_relation "
            "ON requirement_relations ("
            "consolidation_id, source_requirement_id, target_requirement_id, "
            "relation_type)"
        )


def _migrate_sqlite_consolidations_hierarchy_status(engine: Engine) -> None:
    """以可重复执行的ALTER TABLE为归并批次补充P0-4B层级状态列。"""
    inspector = inspect(engine)
    if not inspector.has_table("job_consolidations"):
        return
    existing_columns = {
        column["name"] for column in inspector.get_columns("job_consolidations")
    }
    if "hierarchy_status" in existing_columns:
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE job_consolidations "
            "ADD COLUMN hierarchy_status VARCHAR(30) NOT NULL DEFAULT 'success'"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_job_consolidations_hierarchy_status "
            "ON job_consolidations (hierarchy_status)"
        )


def _rebuild_sqlite_consolidation_relations(engine: Engine) -> None:
    """删除并重建可再生的关系表，移除旧枚举数据（is_a/part_of/related_to）。

    关系表完全由LLM归并批次再生，不承载用户私有数据；旧关系类型
    已从枚举删除（来源无法确认的旧评测夹具已移除），旧关系数据不再
    兼容读取。批次主记录与 raw_response 审计输出仍保留。
    """
    inspector = inspect(engine)
    if not inspector.has_table("requirement_relations"):
        return
    relation_table = Base.metadata.tables["requirement_relations"]
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE requirement_relations")
        relation_table.create(connection)
