"""generate-report 市场报告闭环核心测试（离线，不调用模型）。

覆盖模块业务合同：

1. 显式归并批次能够生成报告；
2. 批次不存在时拒绝；
3. 批次不完整或映射损坏时拒绝；
4. 同一 JD 多个实例只计一次 JD 覆盖；
5. JD 覆盖率分母来自批次实际选定 JD；
6. JD 级 importance 优先级正确；
7. 排序稳定；
8. 每个统计项能追溯到 requirement、JD 和 evidence；
9. Markdown 特殊字符和多行 evidence 不破坏报告结构；
10. 相同输入重复生成内容一致；
11. CLI 不初始化或调用 LLM；
12. 公开样例不包含真实 JD、密钥、私有路径或模型原始响应。
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from pathlib import Path

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
    ConsolidatorMetadata,
    load_consolidation_selection,
    persist_consolidation,
    scope_key_for,
)
from app.consolidation_validation import result_fingerprint
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.market_analysis import build_market_statistics
from app.market_report import (
    _evidence_block,
    build_market_report,
    validate_report_inputs,
)
from app.models import (
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)


def _seed_market_db(database_path: Path, *, include_job_4: bool = False) -> None:
    """合成市场数据库：3 份 JD、跨 JD canonical、importance 与特殊字符。"""
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            jobs = []
            job_specs = [
                    ("示例科技", "大模型应用工程师", "北京"),
                    ("示例智能", "Agent 开发工程师", "上海"),
                    ("示例数据", "RAG 平台工程师", "深圳"),
            ]
            if include_job_4:
                job_specs.append(("新增示例", "Agent 工程师", "杭州"))
            for index, (company, title, city) in enumerate(
                job_specs,
                start=1,
            ):
                job = JobDescription(
                    source_hash=f"m{index}" + "a" * (64 - len(f"m{index}")),
                    source_file=f"sample-{index}.md",
                    source_type="test",
                    collected_at=date(2026, 8, 1),
                    company=company,
                    title=title,
                    city=city,
                    company_type="medium_company",
                    tags=[],
                    extra_metadata={},
                    raw_text=f"# {title}\n\n职责与要求。",
                )
                session.add(job)
                jobs.append(job)
            session.flush()

            # 每 JD 一份 v0.10 + Schema V3 抽取。
            # 实例设计：
            #   JD1: 1 编程语言(must) 2 协作能力(must) 3 数据分析经验(preferred)
            #   JD2: 4 编程语言(must) 5 协作能力(preferred) 6 特殊字符(mentioned)
            #   JD3: 7 编程语言(preferred) 8 学历(unknown)
            by_job = {
                jobs[0].id: [
                    ("编程语言", "must", "1. 熟悉主流编程语言。"),
                    ("协作能力", "must", "具备跨团队协作能力。"),
                    ("数据分析经验", "preferred", "有数据分析经验者优先。"),
                ],
                jobs[1].id: [
                    ("编程语言", "must", "掌握常用编程语言。"),
                    ("协作能力", "preferred", "具备良好沟通与协作精神。"),
                    ("特殊字符", "mentioned", "熟悉 `LangChain`、*RAG* 等工具|\n第二行证据（多行）。"),
                ],
                jobs[2].id: [
                    ("编程语言", "preferred", "熟悉编程语言者加分。"),
                    ("学历", "unknown", "本科及以上学历。"),
                ],
            }
            if include_job_4:
                by_job[jobs[3].id] = [
                    ("新增要求", "must", "新增 JD 的独立要求。")
                ]
            requirement_ids: dict[str, list[int]] = {}
            for job in jobs:
                extraction = JobExtraction(
                    job_id=job.id,
                    extractor_version="test-model|prompt:0.10|schema:3.0",
                    model_name="test-model",
                    prompt_version="0.10",
                    schema_version="3.0",
                    role_family="other",
                    seniority="unknown",
                    raw_response={},
                )
                session.add(extraction)
                session.flush()
                for raw_name, importance, evidence in by_job[job.id]:
                    requirement = JobRequirement(
                        extraction_id=extraction.id,
                        raw_name=raw_name,
                        category="other",
                        importance=importance,
                        proficiency="basic",
                        group_id=None,
                        group_logic="standalone",
                        min_years=None,
                        max_years=None,
                        years_text=None,
                        evidence=evidence,
                        confidence=0.9,
                    )
                    session.add(requirement)
                    session.flush()
                    requirement_ids.setdefault(raw_name, []).append(requirement.id)
            session.commit()
            job_ids = {job.id for job in jobs}

        # 归并批次：编程语言跨 3 JD，协作能力跨 2 JD，其余单 JD。
        canonical_items = [
            CanonicalRequirement(
                canonical_requirement_id="cr-lang",
                canonical_name="编程语言",
                source_requirement_ids=sorted(
                    requirement_ids["编程语言"]
                ),
                rationale="合成数据",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-collab",
                canonical_name="团队协作能力",
                source_requirement_ids=sorted(requirement_ids["协作能力"]),
                rationale="合成数据",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-data",
                canonical_name="数据分析经验",
                source_requirement_ids=sorted(requirement_ids["数据分析经验"]),
                rationale="合成数据",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-special",
                canonical_name="特殊字符要求",
                source_requirement_ids=sorted(requirement_ids["特殊字符"]),
                rationale="合成数据",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-edu",
                canonical_name="本科及以上学历",
                source_requirement_ids=sorted(requirement_ids["学历"]),
                rationale="合成数据",
                confidence=0.9,
            ),
        ]
        if include_job_4:
            canonical_items.append(
                CanonicalRequirement(
                    canonical_requirement_id="cr-new",
                    canonical_name="新增要求",
                    source_requirement_ids=sorted(requirement_ids["新增要求"]),
                    rationale="合成数据",
                    confidence=0.9,
                )
            )
        result = RequirementConsolidationResult(
            canonical_requirements=canonical_items,
            mappings=build_mappings_from_canonical_partition(canonical_items),
        )
        with session_factory() as session:
            selection = load_consolidation_selection(session, job_ids=job_ids)
            metadata = ConsolidatorMetadata(
                model_name="test-model",
                prompt_version=CONSOLIDATION_PROMPT_VERSION,
                schema_version=CONSOLIDATION_SCHEMA_VERSION,
            )
            persist_consolidation(
                session,
                selection,
                result,
                {
                    "model_response": {"canonical_requirements": []},
                    "normalized_result": result.model_dump(mode="json"),
                    "review_decisions_fingerprint": "synthetic-review",
                    "source_run_identifier": "run-0",
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-05T00:00:00+00:00",
                    "approved_run_index": 0,
                    "approved_result_fingerprint": result_fingerprint(result),
                    "final_result_fingerprint": result_fingerprint(result),
                },
                metadata,
                scope_key_for(job_ids or None),
            )
    finally:
        engine.dispose()


def _build_stats(database_path: Path) -> object:
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            consolidation_id = session.query(JobConsolidation).one().id
        return build_market_statistics(session_factory, consolidation_id)
    finally:
        engine.dispose()


def test_report_generated_for_explicit_batch(tmp_path) -> None:
    """显式归并批次能够生成完整报告。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    report = build_market_report(stats)

    assert "岗位要求市场分析报告" in report
    assert "样本限制" in report
    assert "流程与证据追溯能力演示" in report
    assert f"#{stats.consolidation_id}" in report
    assert "3" in report  # JD 数
    assert "跨 JD 共同要求" in report
    assert "单 JD 特有要求" in report
    assert "证据追溯" in report
    assert "编程语言" in report
    assert "团队协作能力" in report


def test_build_market_report_renders_provenance_note(tmp_path) -> None:
    """provenance_note 非 None 时渲染为"上游来源绑定"行；默认无该行。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    plain = build_market_report(stats)
    assert "上游来源绑定" not in plain

    noted = build_market_report(stats, provenance_note="JD 1:unverified（无豁免）")
    assert "**上游来源绑定**：JD 1:unverified（无豁免）" in noted


def test_report_rejects_incomplete_consolidation_finalization(tmp_path) -> None:
    """定稿元数据不完整的批次被报告门禁拒绝。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).one()
            record.raw_response = {
                "review_decisions_fingerprint": "synthetic-review",
                "source_run_identifier": "run-0",
            }
            session.commit()
        failures = validate_report_inputs(session_factory, 1)
        assert any("定稿元数据" in failure for failure in failures)
    finally:
        engine.dispose()


def _write_valid_waiver(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "record_type": "legacy_extraction_waiver",
                "schema_version": 1,
                "approved_by": "test-owner",
                "approved_at": "2026-08-07",
                "applicable_records": {"job_ids": [1, 2, 3]},
                "reason": "测试历史记录缺少现行绑定字段。",
                "existing_evidence": ["测试验收与人工审计证据。"],
                "allowed_use": "允许 generate-report 消费 JD 1/2/3。",
                "risk": "来源绑定无法机器证明，报告必须保留风险。",
                "status": "unverified",
                "constraints": {"new_records": "新增 JD 禁止使用。"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_default_waiver_path_is_private() -> None:
    from app.finalization import LEGACY_EXTRACTION_WAIVER_PATH

    assert LEGACY_EXTRACTION_WAIVER_PATH.parts[:2] == ("data", "private")


def _bind_extractions(database_path: Path, job_ids: set[int] | None = None) -> None:
    """把测试夹具中的指定抽取标为 fully_bound。"""
    from app.finalization import EXTRACTION_FINALIZATION_FIELDS

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            for extraction in session.query(JobExtraction).all():
                if job_ids is None or extraction.job_id in job_ids:
                    extraction.raw_response = {
                        field: f"test-{field}"
                        for field in EXTRACTION_FINALIZATION_FIELDS
                    }
            session.commit()
    finally:
        engine.dispose()


def test_cli_generate_report_marks_waived_unbound_upstream(
    tmp_path, monkeypatch
) -> None:
    """JD 1～3 unverified 且 waiver 合法时放行并保留风险提示。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)  # 夹具 extraction raw_response={}（unverified）
    waiver_path = tmp_path / "legacy-waiver.json"
    _write_valid_waiver(waiver_path)
    monkeypatch.setattr(
        cli_module, "LEGACY_EXTRACTION_WAIVER_PATH", waiver_path
    )

    output = tmp_path / "report.md"
    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(output),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "上游来源绑定警告" in result.output
    report = output.read_text(encoding="utf-8")
    assert "**上游来源绑定**：" in report
    assert "unverified" in report


def test_cli_generate_report_rejects_unwaived_new_job(
    tmp_path, monkeypatch
) -> None:
    """waiver 外新增 JD non-fully-bound 时拒绝生成。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path, include_job_4=True)
    waiver_path = tmp_path / "legacy-waiver.json"
    _write_valid_waiver(waiver_path)
    monkeypatch.setattr(
        cli_module, "LEGACY_EXTRACTION_WAIVER_PATH", waiver_path
    )
    output = tmp_path / "report.md"

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(output),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )

    assert result.exit_code == 1
    assert "non-fully-bound" in result.output
    assert "[4]" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "waiver_content",
    [None, "{not-json", json.dumps({"record_type": "wrong"})],
    ids=["missing", "invalid-json", "invalid-contract"],
)
def test_cli_generate_report_rejects_missing_or_invalid_waiver(
    tmp_path, monkeypatch, waiver_content
) -> None:
    """存在 unverified 来源时，waiver 缺失或非法均拒绝。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    waiver_path = tmp_path / "legacy-waiver.json"
    if waiver_content is not None:
        waiver_path.write_text(waiver_content, encoding="utf-8")
    monkeypatch.setattr(
        cli_module, "LEGACY_EXTRACTION_WAIVER_PATH", waiver_path
    )

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(tmp_path / "report.md"),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )

    assert result.exit_code == 1
    assert "historical waiver" in result.output


def test_cli_generate_report_allows_waived_history_and_bound_new_job(
    tmp_path, monkeypatch
) -> None:
    """JD 1～3 unverified + 新 JD fully_bound 时合法 waiver 继续放行。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path, include_job_4=True)
    _bind_extractions(db_path, {4})
    waiver_path = tmp_path / "legacy-waiver.json"
    _write_valid_waiver(waiver_path)
    monkeypatch.setattr(
        cli_module, "LEGACY_EXTRACTION_WAIVER_PATH", waiver_path
    )
    output = tmp_path / "report.md"

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(output),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )

    assert result.exit_code == 0, result.output
    report = output.read_text(encoding="utf-8")
    assert "JD 1:unverified" in report
    assert "JD 4:" not in report


def test_cli_generate_report_clean_upstream_no_note(tmp_path) -> None:
    """上游抽取全部 fully_bound 时报告不含来源绑定警告。"""
    from app.finalization import EXTRACTION_FINALIZATION_FIELDS
    from typer.testing import CliRunner

    from app.cli import cli

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            for extraction in session.query(JobExtraction).all():
                extraction.raw_response = {
                    field: f"test-{field}"
                    for field in EXTRACTION_FINALIZATION_FIELDS
                }
            session.commit()
    finally:
        engine.dispose()

    output = tmp_path / "report-clean.md"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(output),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "上游来源绑定警告" not in result.output
    report = output.read_text(encoding="utf-8")
    assert "**上游来源绑定**：" not in report


def test_report_rejects_missing_batch(tmp_path) -> None:
    """批次不存在时拒绝生成。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        failures = validate_report_inputs(session_factory, 999)
    finally:
        engine.dispose()

    assert any("不存在" in failure for failure in failures)


def test_report_rejects_corrupted_batch(tmp_path) -> None:
    """批次映射损坏（删除一条 mapping）时拒绝生成。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            consolidation_id = session.query(JobConsolidation).one().id
            first = session.query(RequirementMappingRecord).first()
            session.delete(first)
            session.commit()
        failures = validate_report_inputs(session_factory, consolidation_id)
    finally:
        engine.dispose()

    assert failures  # 精确 ID 覆盖校验必须失败


def test_same_job_multiple_instances_counted_once(tmp_path) -> None:
    """同一 JD 多个实例只贡献一次 JD 覆盖。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    programming = next(
        item for item in stats.canonical_items if item.canonical_name == "编程语言"
    )
    assert programming.instance_count == 3
    assert programming.distinct_job_count == 3  # JD1/2/3 各一次

    collaboration = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "团队协作能力"
    )
    assert collaboration.instance_count == 2
    assert collaboration.distinct_job_count == 2  # JD1 + JD2


def test_coverage_denominator_uses_selected_jobs(tmp_path) -> None:
    """JD 覆盖率分母来自批次实际选定 JD。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    assert stats.total_job_count == 3
    assert stats.selected_job_ids == (1, 2, 3)
    programming = next(
        item for item in stats.canonical_items if item.canonical_name == "编程语言"
    )
    assert programming.distinct_job_count / stats.total_job_count == 1.0
    collaboration = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "团队协作能力"
    )
    assert collaboration.distinct_job_count / stats.total_job_count == 2 / 3


def test_importance_priority(tmp_path) -> None:
    """JD 级 importance 按 must > preferred > mentioned > unknown 归并。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    programming = next(
        item for item in stats.canonical_items if item.canonical_name == "编程语言"
    )
    # JD1 must、JD2 must、JD3 preferred → JD 级按优先级归并。
    assert programming.importance_job_counts == {"must": 2, "preferred": 1}
    # 实例级保留完整分布（诊断口径）。
    assert programming.importance_instance_counts == {"must": 2, "preferred": 1}

    collaboration = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "团队协作能力"
    )
    assert collaboration.importance_job_counts == {"must": 1, "preferred": 1}


def test_sorting_stable(tmp_path) -> None:
    """排序稳定且符合（JD 数降序、实例数降序、名称升序）。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    first = _build_stats(db_path)
    second = _build_stats(db_path)

    names_first = [item.canonical_name for item in first.canonical_items]
    names_second = [item.canonical_name for item in second.canonical_items]
    assert names_first == names_second  # 重复计算稳定
    # 排序规则抽查。
    assert names_first[0] == "编程语言"  # 3 JD
    assert names_first[1] == "团队协作能力"  # 2 JD
    keys = [
        (-item.distinct_job_count, -item.instance_count, item.canonical_name)
        for item in first.canonical_items
    ]
    assert keys == sorted(keys)


def test_traceability(tmp_path) -> None:
    """每个统计项可追溯到 requirement、JD 和 evidence。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)
    report = build_market_report(stats)

    collaboration = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "团队协作能力"
    )
    assert collaboration.source_job_ids == (1, 2)
    assert len(collaboration.source_requirements) == 2
    # 报告包含 JD 标签、实例 ID 与 evidence 文本。
    assert "JD 1｜实例" in report
    assert "具备跨团队协作能力" in report
    assert "importance=must" in report


def test_markdown_special_chars_and_multiline(tmp_path) -> None:
    """Markdown 特殊字符与多行 evidence 不破坏报告结构。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)
    report = build_market_report(stats)

    # 特殊字符 evidence 原样保留（转义后展示）。
    assert "LangChain" in report
    assert "RAG" in report
    assert "第二行证据（多行）" in report
    # 表格结构完整：表头 + 共同要求行数（2 个跨 JD canonical）。
    common_table = report.split("## 跨 JD 共同要求")[1].split("## 单 JD")[0]
    rows = [line for line in common_table.splitlines() if line.startswith("|")]
    assert len(rows) == 1 + 1 + 2  # 表头 + 分隔行 + 2 个共同要求
    # 长尾表：3 个单 JD canonical。
    tail_table = report.split("## 单 JD 特有要求")[1].split("## 证据追溯")[0]
    tail_rows = [line for line in tail_table.splitlines() if line.startswith("|")]
    assert len(tail_rows) == 1 + 1 + 3


def test_deterministic_output(tmp_path) -> None:
    """相同输入重复生成内容一致。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    assert build_market_report(stats) == build_market_report(stats)


def test_cli_generate_report_offline(tmp_path, monkeypatch) -> None:
    """CLI 生成报告不初始化 LLM 客户端。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    _bind_extractions(db_path)
    report_path = tmp_path / "report.md"

    def exploding_settings():
        raise AssertionError("不应加载 LLM 配置")

    monkeypatch.setattr(cli_module, "load_llm_settings", exploding_settings)
    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(report_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert report_path.exists()
    assert "报告已生成" in result.output
    assert "sk-" not in result.output


def test_cli_rejects_missing_batch(tmp_path) -> None:
    """CLI 对不存在的批次返回非零。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "999",
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert result.exit_code == 1
    assert "不存在" in result.output


def test_report_rejects_unfinalized_consolidation(tmp_path) -> None:
    """结构合法但缺少审核绑定的候选批次不得生成正式报告。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            raw_response = dict(batch.raw_response)
            raw_response.pop("review_decisions_fingerprint")
            batch.raw_response = raw_response
            session.commit()
        failures = validate_report_inputs(session_factory, 1)
    finally:
        engine.dispose()

    assert any("缺少定稿元数据" in failure for failure in failures)


def test_sample_limitation_is_dynamic(tmp_path) -> None:
    """样本限制声明由当前统计动态生成，不写死真实批次数字。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)
    report = build_market_report(stats)

    # 合成批次身份：3 JD / 8 实例 / 5 canonical。
    assert "3 份 JD" in report
    assert "8 条 requirement instances" in report
    assert "5 个 canonical requirements" in report
    # 顶部声明与报告身份、总览一致。
    assert "8 条 requirement instances" in report  # 动态声明
    assert "requirement instance 数：8" in report  # 报告身份
    assert "抽取原子要求数：8" in report  # 总览
    # 不得写死真实批次的 83/72。
    assert "83 条" not in report
    assert "72 个" not in report
    # 方法与限制章节也动态。
    assert "当前样本为 3 份 JD" in report


def test_evidence_blocks_have_clean_structure(tmp_path) -> None:
    """每个实例形成独立块：主条目独占一行、detail 层级、evidence 引用块。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)

    collaboration = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "团队协作能力"
    )
    block = _evidence_block(collaboration.source_requirements)
    lines = block.splitlines()
    # 主条目独占一行。
    assert lines[0].startswith("- JD 1｜实例 2：**协作能力**")
    # detail 处于该实例下（缩进层级）。
    assert lines[1].startswith("  - importance=")
    assert lines[2].startswith("  - 证据：")
    # evidence 在引用块中且与实例绑定。
    assert lines[3].startswith("    > 具备跨团队协作能力。")
    # 第二个实例独立成块（空行分隔），不紧贴前一 evidence。
    second = [i for i, line in enumerate(lines) if line.startswith("- JD 2｜")]
    assert second and second[0] > 4
    assert "" in block  # 块间空行


def test_multiline_special_evidence_stays_in_block(tmp_path) -> None:
    """多行与特殊字符 evidence 保持在同一引用块内，不破坏表格数。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    stats = _build_stats(db_path)
    report = build_market_report(stats)

    special = next(
        item
        for item in stats.canonical_items
        if item.canonical_name == "特殊字符要求"
    )
    block = _evidence_block(special.source_requirements)
    lines = block.splitlines()
    # 特殊字符行各自独立且同属一个引用块。
    assert "    > 熟悉 \\`LangChain\\`、\\*RAG\\* 等工具|" in lines
    assert "    > 第二行证据（多行）。" in lines
    # 章节与表格数量不变（两个要求表头、章节结构固定）。
    assert report.count("| 要求 | JD 覆盖数 |") == 2
    for section in ("报告身份", "总览", "跨 JD 共同要求", "单 JD 特有要求", "证据追溯", "方法与限制"):
        assert f"## {section}" in report


def test_gate_rejects_empty_canonical(tmp_path) -> None:
    """插入空 canonical（无来源成员）时拒绝生成。"""
    from app.models import CanonicalRequirementRecord

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            session.add(
                CanonicalRequirementRecord(
                    consolidation_id=batch.id,
                    canonical_requirement_id="cr-empty",
                    canonical_name="空条件",
                    source_requirement_ids=[],
                    rationale="测试",
                    confidence=0.9,
                )
            )
            session.commit()
            consolidation_id = batch.id
        failures = validate_report_inputs(session_factory, consolidation_id)
    finally:
        engine.dispose()

    assert failures
    assert any("结构合同" in f or "没有来源" in f for f in failures)


def test_gate_rejects_unknown_canonical_reference(tmp_path) -> None:
    """mapping 引用未知 canonical 时拒绝生成。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            first = session.query(RequirementMappingRecord).first()
            first.canonical_requirement_id = "cr-nonexistent"
            session.commit()
            consolidation_id = batch.id
        failures = validate_report_inputs(session_factory, consolidation_id)
    finally:
        engine.dispose()

    assert failures
    assert any("结构合同" in f for f in failures)


def test_duplicate_mapping_blocked_at_database_layer(tmp_path) -> None:
    """重复 mapping 由数据库唯一约束拒绝（生产保护，门禁 validator 为防御层）。"""
    import sqlite3

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        indexes = conn.execute(
            "PRAGMA index_list('requirement_mappings')"
        ).fetchall()
        unique = any(row[2] == 1 for row in indexes)  # unique=1
        assert unique, "requirement_mappings 缺少唯一约束"
    finally:
        conn.close()
    # 直接插入重复 mapping 必须被数据库拒绝。
    from app.models import RequirementMappingRecord

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            first = session.query(RequirementMappingRecord).first()
            session.add(
                RequirementMappingRecord(
                    consolidation_id=batch.id,
                    requirement_id=first.requirement_id,
                    canonical_requirement_id=first.canonical_requirement_id,
                    rationale="测试重复",
                    confidence=0.9,
                )
            )
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        engine.dispose()


def test_gate_rejects_missing_selected_job(tmp_path) -> None:
    """批次选定 JD 缺失时拒绝生成。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            job = session.query(JobDescription).filter(JobDescription.id == 3).one()
            session.delete(job)
            session.commit()
            consolidation_id = batch.id
        failures = validate_report_inputs(session_factory, consolidation_id)
    finally:
        engine.dispose()

    assert failures
    assert any(
        "选定 JD 不存在" in f
        or "requirement 不存在" in f
        or "结构合同" in f  # 级联删除可能先触发结构合同失败
        for f in failures
    )


def test_gate_rejects_requirement_job_out_of_scope(tmp_path) -> None:
    """requirement 来源 JD 超出批次范围时拒绝生成。"""
    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            # 构造存在但超出批次范围的 JD 99 + extraction，再改指 requirement。
            out_job = JobDescription(
                source_hash="o" + "x" * 63,
                source_file="out-of-scope.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="范围外公司",
                title="范围外岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# 范围外岗位",
            )
            session.add(out_job)
            session.flush()
            out_extraction = JobExtraction(
                job_id=out_job.id,
                extractor_version="test-model|prompt:0.10|schema:3.0",
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
            session.add(out_extraction)
            session.flush()
            requirement = session.query(JobRequirement).first()
            requirement.extraction_id = out_extraction.id
            session.commit()
            consolidation_id = batch.id
        failures = validate_report_inputs(session_factory, consolidation_id)
    finally:
        engine.dispose()

    assert failures
    assert any("超出批次范围" in f for f in failures)


def test_gate_failure_does_not_overwrite_output(tmp_path) -> None:
    """验证失败时不覆盖已有报告文件。"""
    from typer.testing import CliRunner

    from app import cli as cli_module

    db_path = tmp_path / "market.db"
    _seed_market_db(db_path)
    _bind_extractions(db_path)
    report_path = tmp_path / "report.md"

    runner = CliRunner()
    first = runner.invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(report_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert first.exit_code == 0
    original = report_path.read_text(encoding="utf-8")

    # 破坏批次后再次生成：必须失败且不覆盖已有文件。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            first_mapping = session.query(RequirementMappingRecord).first()
            session.delete(first_mapping)
            session.commit()
    finally:
        engine.dispose()

    second = runner.invoke(
        cli_module.cli,
        [
            "generate-report",
            "--consolidation-id",
            "1",
            "--output",
            str(report_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert second.exit_code == 1
    assert report_path.read_text(encoding="utf-8") == original


def test_sample_report_does_not_leak_real_batch_numbers(tmp_path) -> None:
    """公开样例与真实统计数字不交叉污染（样例无 83/72）。"""
    import scripts.make_sample_report as sample_script

    output_path = tmp_path / "sample.md"
    assert sample_script.main(["--output", str(output_path)]) == 0
    content = output_path.read_text(encoding="utf-8")
    assert "3 份 JD" in content
    assert "9 条 requirement instances" in content
    assert "6 个 canonical requirements" in content
    assert "83 条" not in content
    assert "72 个" not in content
    # 证据块结构在样例中同样成立。
    assert "    > " in content
    # 合成字段遵守当前 FIELD 合同，不用 other/basic 占位覆盖所有类型。
    assert "category=programming\\_language / proficiency=advanced" in content
    assert "category=experience / proficiency=unknown" in content
    assert "category=education / proficiency=unknown" in content
    assert "category=soft\\_skill / proficiency=unknown" in content


def test_sample_report_is_public_safe(tmp_path) -> None:
    """公开样例不包含真实 JD、密钥、私有路径或模型原始响应。"""
    import scripts.make_sample_report as sample_script

    output_path = tmp_path / "sample.md"
    assert sample_script.main(["--output", str(output_path)]) == 0

    content = output_path.read_text(encoding="utf-8")
    for forbidden in (
        "data/private",
        "data/raw_jds",
        "sk-",
        "model_response",
        "raw_response",
        "C:\\Users",
        "D:\\MyAIWork",
    ):
        assert forbidden not in content, forbidden
    assert "岗位要求市场分析报告" in content
    assert "样本限制" in content
    assert "流程与证据追溯能力演示" in content
