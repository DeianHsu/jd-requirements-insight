"""该模块验证JD解析、内容哈希、幂等导入和单文件错误隔离。"""

from pathlib import Path

from sqlalchemy import func, select

from app.database import create_database_engine, create_session_factory, initialize_database
from app.ingestion import content_hash, import_directory, parse_job_file
from app.models import JobDescription


def write_jd(path: Path, *, company: str = "示例公司", title: str = "AI应用工程师") -> None:
    """向指定路径写入一份格式合法且字段可定制的测试JD。"""
    path.write_text(
        f"""---
source_url: https://example.com/jobs/1
source_type: test
collected_at: 2026-07-21
company: {company}
title: {title}
city: 上海
company_type: medium_company
---

# {title}

负责 Python、RAG 和 Agent 工具调用开发。
""",
        encoding="utf-8",
    )


def make_session_factory(tmp_path: Path):
    """在临时目录创建独立SQLite数据库，并返回Engine和Session工厂。"""
    database_path = tmp_path / "test.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def test_parse_job_file_reads_metadata_and_body(tmp_path: Path) -> None:
    """验证解析器可以同时读取Front Matter元数据和完整Markdown正文。"""
    path = tmp_path / "jd.md"
    write_jd(path)

    document = parse_job_file(path)

    assert document.company == "示例公司"
    assert document.title == "AI应用工程师"
    assert document.city == "上海"
    assert "Agent 工具调用" in document.raw_text
    assert document.source_file == "jd.md"


def test_content_hash_ignores_unimportant_whitespace() -> None:
    """验证空格和空行等排版差异不会改变JD的内容哈希。"""
    first = "Python  RAG\n\nAgent"
    second = "Python RAG\nAgent"

    assert content_hash(first) == content_hash(second)


def test_import_directory_is_idempotent(tmp_path: Path) -> None:
    """验证同一目录连续导入两次时数据库只保存一条记录。"""
    jd_directory = tmp_path / "jds"
    jd_directory.mkdir()
    write_jd(jd_directory / "jd_001.md")
    engine, session_factory = make_session_factory(tmp_path)

    # 连续执行两次相同导入，用结果计数和数据库记录数共同验证幂等性。
    first = import_directory(jd_directory, session_factory)
    second = import_directory(jd_directory, session_factory)

    assert first.discovered == 1
    assert first.imported == 1
    assert first.skipped == 0
    assert first.failed == 0
    assert second.imported == 0
    assert second.skipped == 1

    # 直接查询数据库，避免只依赖导入函数返回值而漏掉实际重复写入问题。
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(JobDescription))
        saved = session.scalar(select(JobDescription))

    assert count == 1
    assert saved is not None
    assert saved.company == "示例公司"
    assert saved.source_url == "https://example.com/jobs/1"
    assert saved.raw_text.startswith("# AI应用工程师")
    engine.dispose()


def test_invalid_file_does_not_block_valid_files(tmp_path: Path) -> None:
    """验证一个缺少标题的错误文件不会阻止同目录合法JD被导入。"""
    jd_directory = tmp_path / "jds"
    jd_directory.mkdir()
    write_jd(jd_directory / "valid.md")
    (jd_directory / "invalid.md").write_text(
        """---
collected_at: 2026-07-21
company: 缺少标题的公司
---

正文仍然存在。
""",
        encoding="utf-8",
    )
    engine, session_factory = make_session_factory(tmp_path)

    # 同一批次混合合法与错误文件，用于验证文件级故障隔离策略。
    summary = import_directory(jd_directory, session_factory)

    assert summary.discovered == 2
    assert summary.imported == 1
    assert summary.failed == 1
    assert summary.errors[0].source_file == "invalid.md"
    assert "title" in summary.errors[0].message
    engine.dispose()
