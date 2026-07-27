"""该模块验证LLM结构化抽取、证据约束、有限重试和幂等持久化。"""

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import create_database_engine, create_session_factory, initialize_database
from app.extraction import (
    ExtractionError,
    ExtractorMetadata,
    extract_job,
    persist_extraction,
    validate_evidence,
)
from app.models import JobDescription, JobExtraction, JobRequirement, JobResponsibility
from app.schemas import JobExtractionResult


class FakeExtractionClient:
    """按预设顺序返回JSON文本，用于在测试中替代真实且有费用的LLM调用。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存待返回响应并初始化调用次数。"""
        self.responses = responses
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应，并记录提示中包含必要约束和JD原文。"""
        assert "只能依据" in system_prompt
        assert "JD原文" in user_prompt
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


def valid_payload(evidence: str = "熟悉 Python 和 RAG。") -> dict[str, object]:
    """生成一份符合抽取Schema且可按需替换证据的测试响应。"""
    return {
        "role_family": "rag_application",
        "seniority": "junior",
        "responsibilities": [
            {"name": "开发RAG应用", "evidence": "负责知识库问答系统开发。"}
        ],
        "requirements": [
            {
                "raw_name": "Python 和 RAG",
                "category": "rag",
                "importance": "must",
                "proficiency": "familiar",
                "years_required": None,
                "evidence": evidence,
                "confidence": 0.95,
            }
        ],
    }


def make_job() -> JobDescription:
    """创建一份包含连续证据文本的内存JD对象供抽取测试使用。"""
    return JobDescription(
        id=1,
        source_hash="a" * 64,
        source_file="sample.md",
        source_type="test",
        collected_at=date(2026, 7, 21),
        company="示例公司",
        title="RAG工程师",
        company_type="medium_company",
        tags=[],
        extra_metadata={},
        raw_text="# RAG工程师\n\n负责知识库问答系统开发。\n\n熟悉 Python 和 RAG。",
    )


def make_database(tmp_path: Path):
    """创建包含全部抽取数据表的临时SQLite数据库。"""
    database_path = tmp_path / "extraction.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def test_extract_job_returns_validated_result() -> None:
    """验证合法模型JSON能够通过Schema和原文证据检查。"""
    client = FakeExtractionClient([valid_payload()])

    result, raw_response = extract_job(make_job(), client)

    assert result.role_family.value == "rag_application"
    assert result.requirements[0].raw_name == "Python 和 RAG"
    assert raw_response["seniority"] == "junior"
    assert client.calls == 1


def test_extract_job_retries_after_invalid_evidence() -> None:
    """验证第一次证据不存在时会把错误反馈给第二次模型请求。"""
    client = FakeExtractionClient(
        [valid_payload("JD中不存在的证据"), valid_payload()]
    )

    result, _ = extract_job(make_job(), client, max_attempts=2)

    assert result.requirements[0].evidence == "熟悉 Python 和 RAG。"
    assert client.calls == 2


def test_validate_evidence_rejects_hallucinated_quote() -> None:
    """验证原文中不存在的职责或要求证据会被明确拒绝。"""
    result = JobExtractionResult.model_validate(valid_payload("不存在的技能要求"))

    with pytest.raises(ExtractionError, match="证据不在JD原文中"):
        validate_evidence(result, make_job().raw_text)


def test_persist_extraction_is_idempotent(tmp_path: Path) -> None:
    """验证同一JD和抽取器版本重复保存时不会产生第二套结果。"""
    engine, session_factory = make_database(tmp_path)
    job = make_job()
    job.id = None
    with session_factory() as session:
        session.add(job)
        session.commit()
        job_id = job.id

    result = JobExtractionResult.model_validate(valid_payload())
    metadata = ExtractorMetadata(model_name="fake-model")
    with session_factory() as session:
        saved_job = session.get(JobDescription, job_id)
        assert saved_job is not None
        first, first_created = persist_extraction(
            session, saved_job, result, result.model_dump(mode="json"), metadata
        )
        second, second_created = persist_extraction(
            session, saved_job, result, result.model_dump(mode="json"), metadata
        )

    with session_factory() as session:
        extraction_count = session.scalar(select(func.count()).select_from(JobExtraction))
        responsibility_count = session.scalar(
            select(func.count()).select_from(JobResponsibility)
        )
        requirement_count = session.scalar(select(func.count()).select_from(JobRequirement))

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert extraction_count == 1
    assert responsibility_count == 1
    assert requirement_count == 1
    engine.dispose()

