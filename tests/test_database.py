"""该模块验证数据库初始化和旧SQLite结构的非破坏性升级。"""

from pathlib import Path

from sqlalchemy import inspect, text

from app.database import create_database_engine, initialize_database


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
