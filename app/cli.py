"""本地命令行入口：JD 导入/列表、v0.10 + Schema V3 抽取、归并、统计与验证。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_llm_settings
from app.candidates import (
    write_consolidation_candidate,
    write_extraction_candidates,
)
from app.consolidation import (
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    list_consolidations,
    load_consolidation_selection,
)
from app.consolidation_finalization import finalize_consolidation
from app.consolidation_validation import (
    load_persisted_consolidation_result,
    validate_contract,
    validate_persisted_consistency,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
    initialize_database,
    project_database_url,
)
from app.extraction import (
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
    list_extractions,
)
from app.extraction_finalization import finalize_extraction
from app.finalization import (
    audit_consolidation_identity,
    audit_extraction_sources,
)
from app.market_analysis import build_market_statistics
from app.market_report import build_market_report, validate_report_inputs
from app.models import JobDescription
from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from app.ingestion import import_directory, list_jobs

cli = typer.Typer(no_args_is_help=True, help="JD Skill Insight 本地数据工具")
console = Console()


def database_resources(
    database_url: str | None,
    use_project_database: bool,
    *,
    allow_create: bool = False,
):
    """打开显式选择的数据库；只读入口不创建不存在的 SQLite 文件。"""
    if bool(database_url) == use_project_database:
        console.print(
            "[red]必须且只能选择 --database-url 或 "
            "--use-project-database 之一。[/red]"
        )
        raise typer.Exit(code=2)
    target = project_database_url() if use_project_database else database_url
    assert target is not None
    parsed = make_url(target)
    if (
        not allow_create
        and parsed.drivername == "sqlite"
        and parsed.database not in (None, ":memory:")
        and not Path(parsed.database).exists()
    ):
        console.print(f"[red]数据库不存在：{parsed.database}[/red]")
        raise typer.Exit(code=1)
    engine = create_database_engine(target)
    if allow_create:
        initialize_database(engine)
    else:
        assert_current_database_schema(engine)
        if not inspect(engine).get_table_names():
            engine.dispose()
            console.print("[red]数据库尚未初始化；只读命令不会创建业务表。[/red]")
            raise typer.Exit(code=1)
    return engine, create_session_factory(engine)


@cli.command("import-jds")
def import_jds(
    directory: Path = typer.Argument(..., help="包含Markdown JD的目录"),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """把指定目录中的Markdown JD批量导入SQLite并显示汇总结果。"""
    engine, session_factory = database_resources(
        database_url, use_project_database, allow_create=True
    )
    # 无论导入成功还是路径校验失败，都释放Engine持有的数据库连接资源。
    try:
        summary = import_directory(directory, session_factory)
    except (FileNotFoundError, NotADirectoryError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    # 统一展示批处理计数，让用户能够判断是否需要修复数据或重复执行。
    console.print(f"发现 [bold]{summary.discovered}[/bold] 个JD文件")
    console.print(f"成功导入 [green]{summary.imported}[/green]")
    console.print(f"重复跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")

    # 将错误与具体文件关联，避免用户在整个目录中人工排查。
    for error in summary.errors:
        console.print(f"  [red]- {error.source_file}: {error.message}[/red]")

    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("list-jds")
def show_jds(
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """以表格形式列出已导入JD的摘要信息而不输出完整正文。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    # 查询结束后立即释放连接，避免Windows下SQLite文件长期被占用。
    try:
        jobs = list_jobs(session_factory)
    finally:
        engine.dispose()

    if not jobs:
        console.print("数据库中还没有JD。")
        return

    # 表格只展示人工浏览所需摘要，完整原文仍保留在数据库中供后续分析。
    table = Table(title=f"已导入JD（{len(jobs)}）")
    table.add_column("ID", justify="right")
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("城市")
    table.add_column("薪资")
    table.add_column("来源文件")

    for job in jobs:
        table.add_row(
            str(job.id),
            job.company,
            job.title,
            job.city or "-",
            job.salary or "-",
            job.source_file,
        )
    console.print(table)


@cli.command("extract-jds")
def extract_jds(
    execute: bool = typer.Option(
        False, "--execute", help="确认发起付费模型调用（必需）"
    ),
    max_attempts: int = typer.Option(2, min=1, max=5),
    limit: int = typer.Option(3, min=1, help="默认最多抽取的JD数量"),
    all_jobs: bool = typer.Option(False, "--all", help="显式抽取全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只抽取指定JD，可重复传入"
    ),
    candidate_output: Path | None = typer.Option(
        None,
        "--candidate-output",
        help="私有候选 JSON 输出路径；执行付费调用时必需",
    ),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """对选定 JD 执行 v0.10 候选抽取；不写正式抽取表。"""
    if all_jobs and job_ids:
        console.print("[red]--all不能与--job-id同时使用。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()

    # 只读计划：计算选择范围并展示，不初始化LLM客户端、不发起调用。
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        with session_factory() as session:
            jobs = list(session.scalars(select(JobDescription).order_by(JobDescription.id)))
        if job_ids is not None:
            selected = [job for job in jobs if job.id in job_ids]
        elif all_jobs:
            selected = jobs
        else:
            selected = jobs[:limit]
    finally:
        engine.dispose()

    console.print(f"模型：[bold]{settings.model}[/bold]")
    console.print("抽取配置：[bold]v0.10 + Schema V3[/bold]")
    console.print(f"本次选择 [bold]{len(selected)}[/bold] 份JD（付费抽取）")
    if not execute:
        console.print("[yellow]未执行：付费模型调用需要显式 --execute 确认。[/yellow]")
        raise typer.Exit(code=2)

    if candidate_output is None:
        console.print("[red]执行候选抽取必须显式指定 --candidate-output。[/red]")
        raise typer.Exit(code=2)

    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    client = OpenAICompatibleExtractionClient(settings)
    metadata = ExtractorMetadata(model_name=settings.model)
    try:
        payload = write_extraction_candidates(
            selected,
            client,
            metadata,
            candidate_output,
            max_attempts=max_attempts,
        )
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    runs = payload["runs"]
    failures = payload["failures"]
    console.print(f"本次选择 [bold]{len(selected)}[/bold] 份JD")
    console.print(f"候选成功 [green]{len(runs)}[/green]")
    console.print(f"失败 [red]{len(failures)}[/red]")
    console.print(f"候选文件：[cyan]{candidate_output}[/cyan]")
    console.print("[yellow]候选结果未写入正式抽取表，需验收后 finalize。[/yellow]")
    for error in failures:
        console.print(
            f"  [red]- JD {error['job_id']} / {error['source_file']}: "
            f"{error['message']}[/red]"
        )
    if failures:
        raise typer.Exit(code=1)


@cli.command("consolidate-requirements")
def consolidate_requirements_cmd(
    execute: bool = typer.Option(
        False, "--execute", help="确认发起付费模型调用（必需）"
    ),
    max_attempts: int = typer.Option(2, min=1, max=5),
    all_jobs: bool = typer.Option(False, "--all", help="显式归并全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只归并指定JD，可重复传入"
    ),
    extractor_version: str | None = typer.Option(
        None,
        "--extractor-version",
        help="选择覆盖全部目标JD的抽取器版本；存在多个共同版本时必须指定",
    ),
    candidate_output: Path | None = typer.Option(
        None,
        "--candidate-output",
        help="私有候选 JSON 输出路径；执行付费调用时必需",
    ),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """对选定 JD 范围生成一次归并候选，不写正式归并表。

    单次 LLM 聚类输出 canonical requirements（含来源分区）；无法与其他
    实例安全合并时，模型在单次聚类中为该实例创建 singleton canonical
    requirement。mappings 由确定性代码生成。付费调用必须显式--execute
    确认。
    """
    if all_jobs == bool(job_ids):
        console.print("[red]必须且只能选择--all或--job-id之一。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()

    # 只读计划：装配语料池并展示，不初始化LLM客户端、不发起调用。
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        try:
            with session_factory() as session:
                selection = load_consolidation_selection(
                    session,
                    job_ids=set(job_ids) if job_ids else None,
                    extractor_version=extractor_version,
                )
            instance_count = len(selection.consolidation_input.occurrences)
            job_count = len(selection.selected_job_ids)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    console.print(f"模型：[bold]{settings.model}[/bold]")
    console.print(f"抽取器版本：[bold]{selection.extractor_version}[/bold]")
    console.print(
        f"本次归并 [bold]{instance_count}[/bold] 条要求实例"
        f" / {job_count} 份JD（付费调用）"
    )
    if not execute:
        console.print("[yellow]未执行：付费模型调用需要显式 --execute 确认。[/yellow]")
        raise typer.Exit(code=2)

    if candidate_output is None:
        console.print("[red]执行候选归并必须显式指定 --candidate-output。[/red]")
        raise typer.Exit(code=2)

    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    client = OpenAICompatibleConsolidationClient(settings)
    metadata = ConsolidatorMetadata(model_name=settings.model)
    try:
        payload = write_consolidation_candidate(
            selection,
            client,
            metadata,
            candidate_output,
            max_attempts=max_attempts,
        )
    except (FileExistsError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    result = payload["result"]
    console.print(f"语料池 [bold]{instance_count}[/bold] 条要求实例")
    console.print(
        f"候选标准要求项 [bold]{len(result['canonical_requirements'])}[/bold] 个"
    )
    console.print(f"候选文件：[cyan]{candidate_output}[/cyan]")
    console.print("[yellow]候选结果未写入正式归并表，需审核后 finalize。[/yellow]")


@cli.command("list-consolidations")
def show_consolidations(
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """以表格形式列出已持久化的正式归并批次摘要。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        consolidations = list_consolidations(session_factory)
    finally:
        engine.dispose()

    if not consolidations:
        console.print("数据库中还没有归并批次。")
        return

    table = Table(title=f"已持久化归并批次（{len(consolidations)}）")
    table.add_column("ID", justify="right")
    table.add_column("范围")
    table.add_column("归并器版本")
    table.add_column("抽取器版本")
    table.add_column("输入指纹")
    table.add_column("实例数", justify="right")
    table.add_column("标准项", justify="right")
    table.add_column("映射", justify="right")

    for record in consolidations:
        table.add_row(
            str(record.id),
            record.scope_key,
            record.consolidator_version,
            record.extractor_version,
            record.input_fingerprint[:12],
            str(record.occurrence_count),
            str(len(record.canonical_requirements)),
            str(len(record.mappings)),
        )
    console.print(table)


@cli.command("validate-consolidation")
def validate_consolidation_cmd(
    consolidation_id: int = typer.Option(
        ...,
        "--consolidation-id",
        min=1,
        help="显式指定要验证的持久化归并批次ID",
    ),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """离线验证一个已持久化归并批次：合同与真实输入集合一致性。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        persisted = load_persisted_consolidation_result(
            session_factory, consolidation_id
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    contract = validate_contract(
        persisted.result,
        expected_ids=persisted.expected_requirement_ids,
    )
    consistency_failures = validate_persisted_consistency(persisted)

    console.print(f"归并批次ID [bold]{persisted.consolidation_id}[/bold]")
    console.print(f"范围 [bold]{persisted.scope_key}[/bold]")
    console.print(f"归并器版本 [bold]{persisted.consolidator_version}[/bold]")
    console.print(f"真实输入实例数 [bold]{len(persisted.expected_requirement_ids)}[/bold]")
    console.print(
        "P0-4 完整覆盖 "
        + (f"[green]{contract.coverage:.2%}[/green]"
           if contract.coverage == 1.0 else
           f"[red]{contract.coverage:.2%}[/red]")
    )
    console.print(
        "P0-4 结构违规 "
        + (f"[green]{contract.structural_violation_count}[/green]"
           if contract.structural_violation_count == 0 else
           f"[red]{contract.structural_violation_count}[/red]")
    )

    failures: list[str] = []
    if contract.coverage != 1.0:
        failures.append(f"coverage={contract.coverage:.2%}")
    if contract.structural_violation_count != 0:
        failures.append(
            f"structural_violations={contract.structural_violation_count}"
        )
    failures.extend(consistency_failures)
    for failure in failures:
        console.print(f"  [red]- {failure}[/red]")
    if failures:
        raise typer.Exit(code=1)


@cli.command("generate-report")
def generate_report_cmd(
    consolidation_id: int = typer.Option(
        ...,
        "--consolidation-id",
        min=1,
        help="显式指定生成报告使用的持久化归并批次ID",
    ),
    output: Path = typer.Option(
        None,
        "--output",
        help="Markdown 报告输出路径；默认 reports/P0-5/market-report-<id>.md",
    ),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """从显式归并批次离线生成 Markdown 市场分析报告。

    完全离线：不读取 LLM 配置、不调用模型、不需要 --execute。
    生成前执行完整数据一致性门禁（精确 ID 覆盖、mapping 与来源分区
    一致、占位名称检测、requirement→extraction→JD 回查），任何失败
    都拒绝生成并返回非零。报告为可再生派生产物，覆盖已有文件时会
    明确提示。
    """
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        failures = validate_report_inputs(session_factory, consolidation_id)
        if failures:
            console.print("[red]数据完整性门禁未通过，拒绝生成报告：[/red]")
            for failure in failures:
                console.print(f"  [red]- {failure}[/red]")
            raise typer.Exit(code=1)

        stats = build_market_statistics(session_factory, consolidation_id)
    finally:
        engine.dispose()

    report_path = output or Path(
        f"reports/P0-5/market-report-{consolidation_id}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        console.print(
            f"[yellow]报告为可再生派生产物，将覆盖：{report_path}[/yellow]"
        )
    report_path.write_text(
        build_market_report(stats), encoding="utf-8"
    )

    console.print(f"[green]报告已生成：{report_path}[/green]")
    console.print(f"归并批次 #{stats.consolidation_id}（{stats.scope_key}）")
    console.print(f"JD 数 [bold]{stats.total_job_count}[/bold]，"
                  f"实例数 [bold]{stats.occurrence_count}[/bold]，"
                  f"canonical 数 [bold]{stats.canonical_count}[/bold]")
    common = [item for item in stats.canonical_items if item.distinct_job_count > 1]
    if common:
        console.print(
            f"高频要求：{common[0].canonical_name}（"
            f"{common[0].distinct_job_count}/{stats.total_job_count} 份 JD）"
        )


@cli.command("list-extractions")
def show_extractions(
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """列出已持久化的结构化抽取版本及职责和要求数量。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        extractions = list_extractions(session_factory)
    finally:
        engine.dispose()

    if not extractions:
        console.print("数据库中还没有结构化抽取结果。")
        return

    table = Table(title=f"JD结构化抽取（{len(extractions)}）")
    table.add_column("ID", justify="right")
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("方向")
    table.add_column("级别")
    table.add_column("要求", justify="right")
    table.add_column("模型")
    for extraction in extractions:
        table.add_row(
            str(extraction.id),
            extraction.job.company,
            extraction.job.title,
            extraction.role_family,
            extraction.seniority,
            str(len(extraction.requirements)),
            extraction.model_name,
        )
    console.print(table)


@cli.command("audit-extraction-sources")
def audit_extraction_sources_cmd(
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """离线分类正式抽取的来源绑定状态，不修改数据库。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        items = audit_extraction_sources(session_factory)
    finally:
        engine.dispose()

    if not items:
        console.print("数据库中还没有正式抽取记录。")
        return
    table = Table(title=f"正式抽取来源审计（{len(items)}）")
    table.add_column("Extraction", justify="right")
    table.add_column("JD", justify="right")
    table.add_column("状态")
    table.add_column("缺失字段")
    for item in items:
        table.add_row(
            str(item.extraction_id),
            str(item.job_id),
            item.status,
            ", ".join(item.missing_fields) or "-",
        )
    console.print(table)


@cli.command("audit-consolidation")
def audit_consolidation_cmd(
    consolidation_id: int = typer.Option(..., "--consolidation-id", min=1),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """只读显示一个正式归并批次的脱敏身份与可报告状态。"""
    engine, session_factory = database_resources(database_url, use_project_database)
    try:
        try:
            identity = audit_consolidation_identity(
                session_factory, consolidation_id
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    for key in (
        "consolidation_id",
        "scope_key",
        "selected_job_ids",
        "extraction_ids",
        "extractor_version",
        "consolidator_version",
        "input_fingerprint",
        "result_fingerprint",
        "review_decisions_fingerprint",
        "source_run_identifier",
        "occurrence_count",
        "canonical_count",
        "mapping_count",
        "reportable",
    ):
        value = identity[key]
        if key.endswith("fingerprint") and isinstance(value, str):
            value = value[:16] + "…"
        console.print(f"{key}: {value}")
    for failure in identity["failures"]:
        console.print(f"  [red]- {failure}[/red]")
    if not identity["reportable"]:
        raise typer.Exit(code=1)


@cli.command("finalize-extraction")
def finalize_extraction_cmd(
    report: Path = typer.Option(..., "--report"),
    raw_output: Path = typer.Option(..., "--raw-output"),
    job_id: int = typer.Option(..., "--job-id", min=1),
    run_index: int = typer.Option(0, "--run-index", min=0),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """从已审核验收产物离线定稿正式抽取，不调用模型。"""
    engine, _ = database_resources(database_url, use_project_database)
    target = str(engine.url)
    engine.dispose()
    if finalize_extraction(
        report_path=report,
        raw_output_path=raw_output,
        job_id=job_id,
        run_index=run_index,
        database_url=target,
    ):
        raise typer.Exit(code=1)


@cli.command("finalize-consolidation")
def finalize_consolidation_cmd(
    report: Path = typer.Option(..., "--report"),
    raw_output: Path = typer.Option(..., "--raw-output"),
    run_index: int = typer.Option(0, "--run-index", min=0),
    final_result: Path | None = typer.Option(None, "--final-result"),
    review_decisions: Path | None = typer.Option(None, "--review-decisions"),
    database_url: str | None = typer.Option(None, "--database-url"),
    use_project_database: bool = typer.Option(False, "--use-project-database"),
) -> None:
    """从已审核验收产物离线定稿正式归并，不调用模型。"""
    engine, _ = database_resources(database_url, use_project_database)
    target = str(engine.url)
    engine.dispose()
    if finalize_consolidation(
        report_path=report,
        raw_output_path=raw_output,
        run_index=run_index,
        final_result_path=final_result,
        review_decisions_path=review_decisions,
        database_url=target,
    ):
        raise typer.Exit(code=1)


def main() -> None:
    """启动Typer命令行应用并分派用户输入的子命令。"""
    cli()


if __name__ == "__main__":
    main()
