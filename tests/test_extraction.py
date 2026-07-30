"""该模块验证LLM结构化抽取、证据约束、有限重试和幂等持久化。"""

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.database import create_database_engine, create_session_factory, initialize_database
from app.extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    ExtractionError,
    ExtractorMetadata,
    build_user_prompt,
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
        assert "每个requirement只能表达一个" in system_prompt
        assert "group_logic=any_of" in system_prompt
        assert "示例本身不能自动成为独立要求" in system_prompt
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
                "raw_name": "Python",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "familiar",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": evidence,
                "confidence": 0.95,
            },
            {
                "raw_name": "RAG",
                "category": "rag",
                "importance": "must",
                "proficiency": "familiar",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
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


def test_prompt_v2_3_contains_atomic_extraction_boundaries() -> None:
    """验证Prompt V2.3保留要求原子化、任选关系、示例边界和原始要求规则。"""
    assert PROMPT_VERSION == "2.3"
    assert SCHEMA_VERSION == "2.0"
    assert "熟悉Python和RAG" in SYSTEM_PROMPT
    assert "LangChain使用经验" in SYSTEM_PROMPT
    assert "Llama和ChatGLM只是模型示例" in SYSTEM_PROMPT
    assert "proficiency使用unknown" in SYSTEM_PROMPT


def test_prompt_v2_3_balances_responsibility_atomicity_and_business_boundaries() -> None:
    """验证Prompt V2.3先识别交付结果，再平衡职责拆分与合并边界。"""
    assert "构建智能体工作流" in SYSTEM_PROMPT
    assert "实现文献检索自动化" in SYSTEM_PROMPT
    assert "实现实验数据分析自动化" in SYSTEM_PROMPT
    assert "实现合规报告生成自动化" in SYSTEM_PROMPT
    assert "不同对象、不同交付物或可独立验收的业务结果" in SYSTEM_PROMPT
    assert "设计、开发与落地AI Agent管理平台" in SYSTEM_PROMPT
    assert "实施方式" in SYSTEM_PROMPT
    assert "Agent和智能助手只是企业内部AI应用示例" in SYSTEM_PROMPT
    assert "先识别候选动作、对象和结果，再判断职责边界" in SYSTEM_PROMPT
    assert "不能仅因共享同一技术对象就合并" in SYSTEM_PROMPT
    assert "每个原文分句" in SYSTEM_PROMPT
    assert "调研AI模型" in SYSTEM_PROMPT
    assert "选型AI模型" in SYSTEM_PROMPT
    assert "微调AI模型" in SYSTEM_PROMPT
    assert "部署落地AI模型" in SYSTEM_PROMPT
    assert "优化模型效果与推理性能" in SYSTEM_PROMPT


def test_prompt_v2_3_splits_independently_evaluable_conjunctions() -> None:
    """验证Prompt V2.3继续拆开由连接词并列的可独立评价要求。"""
    assert "代码风格" in SYSTEM_PROMPT
    assert "工程素养" in SYSTEM_PROMPT
    assert "复杂系统实现能力" in SYSTEM_PROMPT
    assert "不能把整句或整段直接复制成一个name" in SYSTEM_PROMPT


def test_prompt_v2_3_groups_preferred_alternatives() -> None:
    """验证Prompt V2.3继续把任选加分语言和项目经验放入any_of组。"""
    assert "Python / Node.js 优先" in SYSTEM_PROMPT
    assert "相关项目经验者优先" in SYSTEM_PROMPT
    assert "共享同一个any_of组" in SYSTEM_PROMPT


def test_build_user_prompt_contains_schema_v2_and_retry_feedback() -> None:
    """验证用户Prompt携带V2字段、JD原文和上一轮错误以支持定向修正。"""
    prompt = build_user_prompt(make_job(), "any_of组至少需要两个成员")

    assert '"group_id"' in prompt
    assert '"group_logic"' in prompt
    assert '"min_years"' in prompt
    assert '"max_years"' in prompt
    assert '"years_text"' in prompt
    assert "熟悉 Python 和 RAG。" in prompt
    assert "any_of组至少需要两个成员" in prompt


def test_extract_job_returns_validated_result() -> None:
    """验证合法模型JSON能够通过Schema和原文证据检查。"""
    client = FakeExtractionClient([valid_payload()])

    result, raw_response = extract_job(make_job(), client)

    assert result.role_family.value == "rag_application"
    assert [item.raw_name for item in result.requirements] == ["Python", "RAG"]
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
    assert requirement_count == 2
    engine.dispose()
