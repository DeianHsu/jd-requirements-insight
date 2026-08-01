"""该模块验证数据库初始化和旧SQLite结构的非破坏性升级。"""

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


def test_initialize_database_migrates_legacy_requirement_columns(tmp_path: Path) -> None:
    """验证旧要求表会补齐V2字段并把历史最低年限回填到min_years。"""
    database_path = tmp_path / "legacy.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        # 人工创建最小旧表，准确复现数据库结构V1只有years_required的状态。
        connection.execute(
            text(
                "CREATE TABLE job_requirements ("
                "id INTEGER PRIMARY KEY, years_required FLOAT)"
            )
        )
        connection.execute(
            text("INSERT INTO job_requirements (id, years_required) VALUES (1, 3)")
        )

    initialize_database(engine)

    column_names = {
        column["name"] for column in inspect(engine).get_columns("job_requirements")
    }
    index_names = {
        index["name"] for index in inspect(engine).get_indexes("job_requirements")
    }
    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                "SELECT group_logic, min_years, max_years, years_text "
                "FROM job_requirements WHERE id = 1"
            )
        ).one()

    assert {"group_id", "group_logic", "min_years", "max_years", "years_text"} <= (
        column_names
    )
    assert migrated.group_logic == "standalone"
    assert migrated.min_years == 3
    assert migrated.max_years is None
    assert migrated.years_text is None
    assert "ix_job_requirements_group_id" in index_names
    engine.dispose()


def test_initialize_database_migrates_legacy_consolidation_identity(
    tmp_path: Path,
) -> None:
    """验证旧归并批次保留，并升级为包含输入指纹的三列唯一身份。"""
    database_path = tmp_path / "legacy_consolidation.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE job_consolidations ("
                "id INTEGER PRIMARY KEY, scope_key VARCHAR(255) NOT NULL, "
                "consolidator_version VARCHAR(255) NOT NULL, "
                "model_name VARCHAR(255) NOT NULL, prompt_version VARCHAR(50) NOT NULL, "
                "schema_version VARCHAR(50) NOT NULL, occurrence_count INTEGER NOT NULL, "
                "raw_response JSON NOT NULL, created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_scope_consolidator_version UNIQUE "
                "(scope_key, consolidator_version))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_consolidations VALUES "
                "(1, 'all', 'model|prompt:1.4|schema:1.0', 'model', '1.4', "
                "'1.0', 2, '{}', '2026-08-01 00:00:00')"
            )
        )

    initialize_database(engine)
    initialize_database(engine)

    inspector = inspect(engine)
    columns = {
        column["name"] for column in inspector.get_columns("job_consolidations")
    }
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("job_consolidations")
    }
    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                "SELECT input_fingerprint, extractor_version, selected_job_ids, "
                "extraction_ids FROM job_consolidations WHERE id = 1"
            )
        ).one()

    assert {
        "input_fingerprint",
        "extractor_version",
        "selected_job_ids",
        "extraction_ids",
    } <= columns
    assert len(migrated.input_fingerprint) == 64
    assert migrated.extractor_version == "legacy:unknown"
    assert migrated.selected_job_ids == "[]"
    assert migrated.extraction_ids == "[]"
    assert "uq_scope_consolidator_input" in unique_constraints
    engine.dispose()
