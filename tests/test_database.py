"""该模块验证数据库初始化：外键启用与当前表结构创建。"""

from pathlib import Path

from sqlalchemy import inspect, text

from app.database import create_database_engine, initialize_database


def test_sqlite_connections_enable_foreign_keys() -> None:
    """验证每个SQLite连接都实际启用外键约束。"""
    engine = create_database_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        enabled = connection.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert enabled == 1
    engine.dispose()


def test_initialize_database_creates_current_tables(tmp_path: Path) -> None:
    """验证初始化只创建当前表结构，不包含已删除的关系/职责表。"""
    database_path = tmp_path / "fresh.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    initialize_database(engine)
    # 可重复执行（幂等）。
    initialize_database(engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert {
        "job_descriptions",
        "job_extractions",
        "job_requirements",
        "job_consolidations",
        "canonical_requirements",
        "requirement_mappings",
    } <= tables
    # 已删除的功能不留表。
    assert "requirement_relations" not in tables
    assert "job_responsibilities" not in tables

    consolidation_columns = {
        column["name"]
        for column in inspector.get_columns("job_consolidations")
    }
    assert "hierarchy_status" not in consolidation_columns

    mapping_columns = {
        column["name"] for column in inspector.get_columns("requirement_mappings")
    }
    assert {"requirement_id", "canonical_requirement_id", "rationale", "confidence"} <= (
        mapping_columns
    )
    assert "status" not in mapping_columns
    assert "candidate_requirement_ids" not in mapping_columns
    engine.dispose()
