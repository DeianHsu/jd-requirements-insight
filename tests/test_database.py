"""该模块验证数据库初始化：外键启用、当前表结构创建与旧派生结构拒绝。"""

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    initialize_database,
)


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


def _create_legacy_table(database_path: Path, create_statement: str) -> None:
    """在空数据库中手工创建旧派生结构。"""
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(create_statement))
    engine.dispose()


def test_legacy_relation_table_is_rejected(tmp_path: Path) -> None:
    """含 requirement_relations 表的旧数据库被明确拒绝。"""
    database_path = tmp_path / "legacy_relations.db"
    _create_legacy_table(
        database_path,
        "CREATE TABLE requirement_relations (id INTEGER PRIMARY KEY)",
    )
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    with pytest.raises(RuntimeError, match="旧派生数据库结构"):
        initialize_database(engine)
    engine.dispose()


def test_legacy_mapping_columns_are_rejected(tmp_path: Path) -> None:
    """含旧 mapping 字段（status/candidate_requirement_ids）的库被拒绝。"""
    database_path = tmp_path / "legacy_mapping.db"
    _create_legacy_table(
        database_path,
        "CREATE TABLE requirement_mappings ("
        "id INTEGER PRIMARY KEY, status VARCHAR(30), "
        "candidate_requirement_ids JSON)",
    )
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    with pytest.raises(RuntimeError, match="旧派生数据库结构"):
        initialize_database(engine)
    engine.dispose()


def test_legacy_hierarchy_column_is_rejected(tmp_path: Path) -> None:
    """含 job_consolidations.hierarchy_status 的库被拒绝。"""
    database_path = tmp_path / "legacy_hierarchy.db"
    _create_legacy_table(
        database_path,
        "CREATE TABLE job_consolidations ("
        "id INTEGER PRIMARY KEY, hierarchy_status VARCHAR(30))",
    )
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    with pytest.raises(RuntimeError, match="旧派生数据库结构"):
        initialize_database(engine)
    engine.dispose()


def test_rejection_message_includes_backup_and_rebuild_hint() -> None:
    """错误信息包含备份与重新生成提示（不自动删除、不迁移）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "legacy.db"
        _create_legacy_table(
            database_path,
            "CREATE TABLE requirement_relations (id INTEGER PRIMARY KEY)",
        )
        engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

        with pytest.raises(RuntimeError) as exc_info:
            initialize_database(engine)
        message = str(exc_info.value)
        assert "当前代码只支持 v0.8 + Schema V3" in message
        assert "备份 data/raw_jds/" in message
        assert "删除旧派生数据库并重新生成" in message
        engine.dispose()


def test_assert_current_database_schema_passes_on_fresh_database(
    tmp_path: Path,
) -> None:
    """全新数据库通过结构检查。"""
    database_path = tmp_path / "fresh_check.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)

    assert_current_database_schema(engine)  # 不抛异常
    engine.dispose()
