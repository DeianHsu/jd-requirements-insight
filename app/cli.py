"""该模块提供本地JD导入和列表查看的命令行界面。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_llm_settings
from app.consolidation import (
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    consolidate_requirements,
)
from app.database import create_database_engine, create_session_factory, initialize_database
from app.evaluation import (
    ItemMatchMetrics,
    combine_metrics,
    evaluate_annotation_cases,
    evaluate_extraction,
    load_annotation_cases_file,
    load_golden_file,
    validate_golden_directory,
)
from app.extraction import (
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
    extract_jobs,
    list_extractions,
)
from app.ingestion import import_directory, list_jobs
from app.schemas import JobExtractionResult

cli = typer.Typer(no_args_is_help=True, help="JD Skill Insight 本地数据工具")
console = Console()


def format_accuracy(value: float, total: int) -> str:
    """把无适用样本的准确率显示为N/A，避免与真实零准确率混淆。"""
    return f"{value:.2%}" if total else "N/A"


def format_item_metrics(metrics: ItemMatchMetrics) -> str:
    """格式化原子项P/R/F1，并在预测与期望都为空时显示N/A。"""
    if metrics.predicted == 0 and metrics.expected == 0:
        return "N/A"
    return f"{metrics.precision:.2%} / {metrics.recall:.2%} / {metrics.f1:.2%}"


def select_visible_issues(issues: list[str], max_issues: int) -> tuple[list[str], int]:
    """限制终端展示的错误条数，并返回被省略的数量。"""
    visible = issues[:max_issues]
    return visible, len(issues) - len(visible)


def database_resources():
    """为一次CLI调用创建数据库Engine、数据表和Session工厂。"""
    engine = create_database_engine()
    initialize_database(engine)
    return engine, create_session_factory(engine)


@cli.command("import-jds")
def import_jds(directory: Path = typer.Argument(..., help="包含Markdown JD的目录")) -> None:
    """把指定目录中的Markdown JD批量导入SQLite并显示汇总结果。"""
    engine, session_factory = database_resources()
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
def show_jds() -> None:
    """以表格形式列出已导入JD的摘要信息而不输出完整正文。"""
    engine, session_factory = database_resources()
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


@cli.command("validate-golden")
def validate_golden(
    golden_directory: Path = typer.Argument(..., help="人工标准答案JSON所在目录"),
    raw_jd_directory: Path = typer.Argument(..., help="原始Markdown JD所在目录"),
) -> None:
    """验证人工标准答案的抽取数据合同、来源文件和原文证据是否有效。"""
    summary = validate_golden_directory(golden_directory, raw_jd_directory)
    console.print(f"发现 [bold]{summary.discovered}[/bold] 个人工标准答案文件")
    console.print(f"校验通过 [green]{summary.valid}[/green]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- {error}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("extract-jds")
def extract_jds(
    max_attempts: int = typer.Option(2, min=1, max=5),
    limit: int = typer.Option(3, min=1, help="开发模式最多抽取的JD数量"),
    all_jobs: bool = typer.Option(False, "--all", help="显式抽取全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只抽取指定JD，可重复传入"
    ),
) -> None:
    """默认小批量抽取JD，也可显式选择全部或指定ID。"""
    if all_jobs and job_ids:
        console.print("[red]--all不能与--job-id同时使用。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    engine, session_factory = database_resources()
    try:
        # 客户端和版本元数据在批次开始时固定，保证整批结果可以复现和比较。
        client = OpenAICompatibleExtractionClient(settings)
        metadata = ExtractorMetadata(model_name=settings.model)
        summary = extract_jobs(
            session_factory,
            client,
            metadata,
            max_attempts=max_attempts,
            limit=None if all_jobs or job_ids else limit,
            job_ids=set(job_ids) if job_ids else None,
        )
    finally:
        engine.dispose()

    console.print(f"本次选择 [bold]{summary.discovered}[/bold] 份JD")
    console.print(f"成功抽取 [green]{summary.extracted}[/green]")
    console.print(f"同版本跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- JD {error.job_id} / {error.source_file}: {error.message}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("consolidate-requirements")
def consolidate_requirements_cmd(
    max_attempts: int = typer.Option(2, min=1, max=5),
    all_jobs: bool = typer.Option(False, "--all", help="显式归并全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只归并指定JD，可重复传入"
    ),
) -> None:
    """对选定JD范围内的要求实例执行跨JD原子要求归并并幂等保存。"""
    if all_jobs and job_ids:
        console.print("[red]--all不能与--job-id同时使用。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    engine, session_factory = database_resources()
    try:
        # 客户端和版本元数据在批次开始时固定，保证整批结果可以复现和比较。
        client = OpenAICompatibleConsolidationClient(settings)
        metadata = ConsolidatorMetadata(model_name=settings.model)
        summary = consolidate_requirements(
            session_factory,
            client,
            metadata,
            max_attempts=max_attempts,
            job_ids=set(job_ids) if job_ids else None,
        )
    finally:
        engine.dispose()

    console.print(f"语料池 [bold]{summary.discovered}[/bold] 条要求实例")
    console.print(f"归并成功 [green]{summary.consolidated}[/green] 条")
    console.print(f"标准要求项 [bold]{summary.canonical_count}[/bold] 个")
    console.print(f"要求关系 [bold]{summary.relation_count}[/bold] 条")
    console.print(f"同版本跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- {error.scope}: {error.message}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("list-extractions")
def show_extractions() -> None:
    """列出已持久化的结构化抽取版本及职责和要求数量。"""
    engine, session_factory = database_resources()
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
    table.add_column("职责", justify="right")
    table.add_column("要求", justify="right")
    table.add_column("模型")
    for extraction in extractions:
        table.add_row(
            str(extraction.id),
            extraction.job.company,
            extraction.job.title,
            extraction.role_family,
            extraction.seniority,
            str(len(extraction.responsibilities)),
            str(len(extraction.requirements)),
            extraction.model_name,
        )
    console.print(table)


@cli.command("evaluate-extractions")
def evaluate_extractions(
    golden_directory: Path = typer.Argument(..., help="人工标准答案JSON所在目录"),
) -> None:
    """把数据库中每份JD的最新抽取结果与人工标准答案进行对比评测。"""
    engine, session_factory = database_resources()
    try:
        persisted = list_extractions(session_factory)
    finally:
        engine.dispose()

    # 同一JD存在多个版本时选取ID最大的最新记录，避免重复计入总体指标。
    latest_by_source = {}
    for extraction in persisted:
        latest_by_source[extraction.job.source_file] = extraction

    metrics = []
    missing_sources = []
    for path in sorted(golden_directory.glob("*.json")):
        golden = load_golden_file(path)
        extraction = latest_by_source.get(golden.source_file)
        if extraction is None:
            missing_sources.append(golden.source_file)
            continue
        predicted = JobExtractionResult.model_validate(extraction.raw_response)
        metrics.append(evaluate_extraction(predicted, golden.extraction))

    if not metrics:
        console.print("[yellow]没有可与人工标准答案对比的抽取结果。[/yellow]")
        raise typer.Exit(code=1)

    combined = combine_metrics(metrics)
    console.print(f"参与评测JD：[bold]{len(metrics)}[/bold]")
    console.print(f"Precision：[cyan]{combined.precision:.2%}[/cyan]")
    console.print(f"Recall：[cyan]{combined.recall:.2%}[/cyan]")
    console.print(f"F1：[cyan]{combined.f1:.2%}[/cyan]")
    console.print(f"重要程度准确率：[cyan]{combined.importance_accuracy:.2%}[/cyan]")
    if missing_sources:
        console.print(f"[yellow]缺少抽取结果：{', '.join(missing_sources)}[/yellow]")


@cli.command("evaluate-cases")
def evaluate_cases(
    cases_file: Path = typer.Argument(..., help="困难样例annotation_cases.json路径"),
    prompt_version: str = typer.Option(..., "--prompt-version", help="待评测Prompt版本"),
    schema_version: str = typer.Option("2.0", "--schema-version"),
    model_name: str | None = typer.Option(None, "--model", help="待评测模型名称"),
    dataset_split: str | None = typer.Option(
        None, "--split", help="只评测指定数据集分组，如development或validation"
    ),
    max_issues: int = typer.Option(
        10, "--max-issues", min=0, help="最多显示的错误摘要条数"
    ),
) -> None:
    """对指定抽取版本运行困难样例的原子项和字段级分层评测。"""
    engine, session_factory = database_resources()
    try:
        persisted = list_extractions(session_factory)
    finally:
        engine.dispose()

    selected = [
        extraction
        for extraction in persisted
        if extraction.prompt_version == prompt_version
        and extraction.schema_version == schema_version
        and (model_name is None or extraction.model_name == model_name)
    ]
    if model_name is None:
        model_names = {extraction.model_name for extraction in selected}
        if len(model_names) > 1:
            console.print("[red]同版本存在多个模型，请使用--model明确指定。[/red]")
            raise typer.Exit(code=1)
        if model_names:
            model_name = next(iter(model_names))
    if not selected:
        console.print("[yellow]没有找到符合指定抽取器版本的结果。[/yellow]")
        raise typer.Exit(code=1)

    try:
        payload = load_annotation_cases_file(cases_file)
        predictions = {
            extraction.job.source_file: JobExtractionResult.model_validate(
                extraction.raw_response
            )
            for extraction in selected
        }
        source_texts = {
            extraction.job.source_file: extraction.job.raw_text for extraction in selected
        }
        summary = evaluate_annotation_cases(
            payload, predictions, source_texts, dataset_split=dataset_split
        )
    except (OSError, ValueError) as exc:
        console.print(f"[red]分层评测失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"评测版本：[bold]{model_name} / prompt:{prompt_version} / "
        f"schema:{schema_version}[/bold]"
    )
    console.print(f"数据分组：[bold]{dataset_split or 'all'}[/bold]")
    console.print(
        f"困难样例：[bold]{summary.evaluated_cases}/{summary.discovered_cases}[/bold]"
    )
    console.print(
        "要求名称代理 P/R/F1："
        f"[cyan]{format_item_metrics(summary.requirement_metrics)}[/cyan]"
    )
    console.print(
        "职责名称代理 P/R/F1："
        f"[cyan]{format_item_metrics(summary.responsibility_metrics)}[/cyan]"
    )
    console.print(
        f"原子项数量一致样例：[cyan]{summary.exact_count_cases}/"
        f"{summary.evaluated_cases}[/cyan]"
    )
    console.print(
        "重要程度准确率："
        f"[cyan]{format_accuracy(summary.importance_accuracy, summary.importance_total)}[/cyan]"
    )
    console.print(
        "熟练度准确率："
        f"[cyan]{format_accuracy(summary.proficiency_accuracy, summary.proficiency_total)}[/cyan]"
    )
    console.print(
        "类别准确率："
        f"[cyan]{format_accuracy(summary.category_accuracy, summary.category_total)}[/cyan]"
    )
    console.print(
        f"年限准确率：[cyan]{format_accuracy(summary.years_accuracy, summary.years_total)}[/cyan]"
    )
    console.print(
        "any_of组准确率："
        f"[cyan]{format_accuracy(summary.any_of_group_accuracy, summary.any_of_groups_total)}[/cyan]"
    )
    console.print(
        "完整结果证据存在率："
        f"[cyan]{format_accuracy(summary.evidence_accuracy, summary.evidence_total)}[/cyan]"
    )
    if summary.missing_sources:
        console.print(
            f"[yellow]缺少来源结果：{', '.join(summary.missing_sources)}[/yellow]"
        )
    visible_issues, omitted_issues = select_visible_issues(summary.issues, max_issues)
    for issue in visible_issues:
        console.print(f"  [yellow]- {issue}[/yellow]")
    if omitted_issues:
        console.print(f"  [yellow]其余 {omitted_issues} 条错误已省略。[/yellow]")


def main() -> None:
    """启动Typer命令行应用并分派用户输入的子命令。"""
    cli()


if __name__ == "__main__":
    main()
