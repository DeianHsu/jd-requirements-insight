"""P0-4分阶段/分块归并：75实例小规模预检。

从抽取器版本2.3.1的149条实例中，选取参考标注覆盖的全部实例并按
requirement_id升序补齐至75条，调用`consolidate_with_correction`执行
分阶段/受控分块归并，记录每阶段请求的实例数、输入输出字符数与耗时，
并与13实例参考标注离线对比（草案已降级，指标仅作参考，不构成验收
门槛）。必须显式`--execute`确认付费模型调用。

输出：脱敏指标报告写入`reports/P0-4/`（仅统计数字，不含真实证据）；
完整归并结果（含原始名称与证据）写入`data/private/experiments/P0-4/`。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.config import load_llm_settings
from app.consolidation import (
    CONSOLIDATION_MAPPING_CHUNK_SIZE,
    ConsolidationError,
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    consolidate_with_correction,
    load_consolidation_selection,
)
from app.consolidation_evaluation import (
    evaluate_consolidation,
    load_consolidation_cases,
)
from app.database import create_database_engine, create_session_factory
from app.requirement_consolidation import (
    RequirementConsolidationInput,
)


class RecordingClient:
    """包装真实LLM客户端，记录每阶段请求的实例数与响应规模。"""

    def __init__(self, inner: OpenAICompatibleConsolidationClient) -> None:
        """保存被包装客户端并初始化请求记录。"""
        self.inner = inner
        self.records: list[dict[str, object]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用真实客户端，并记录实例数、字符数与耗时。"""
        started_at = time.monotonic()
        try:
            payload = json.loads(user_prompt)
            instance_count: int | None = len(payload.get("requirements", []))
        except json.JSONDecodeError:
            # 重试修正提示不是纯JSON（追加了校验错误文本），实例数记为空。
            instance_count = None
        response = self.inner.complete(system_prompt, user_prompt)
        self.records.append(
            {
                "instance_count": instance_count,
                "request_chars": len(user_prompt),
                "response_chars": len(response),
                "response_head": response[:300],
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
        )
        return response


def build_precheck_input(
    selection, cases: dict[str, object], target_size: int = 75
) -> RequirementConsolidationInput:
    """构造覆盖全部参考标注实例并补齐到目标规模的预检输入。"""
    occurrences = sorted(
        selection.consolidation_input.occurrences,
        key=lambda occurrence: occurrence.requirement_id,
    )
    annotated_ids = {
        mapping["requirement_id"] for mapping in cases["expected"]["mappings"]
    }
    chosen = [item for item in occurrences if item.requirement_id in annotated_ids]
    remaining = [item for item in occurrences if item.requirement_id not in annotated_ids]
    for item in remaining:
        if len(chosen) >= target_size:
            break
        chosen.append(item)
    if len(chosen) != target_size:
        raise RuntimeError(
            f"预检输入无法构造{target_size}条（可选{len(occurrences)}条）"
        )
    return RequirementConsolidationInput(occurrences=chosen)


def main() -> int:
    """解析参数、加载输入并执行75实例预检与离线评测。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认发起付费模型调用",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("data/consolidation_cases.json"),
        help="参考标注JSON路径（草案已降级，仅作参考）",
    )
    parser.add_argument(
        "--extractor-version",
        type=str,
        default="deepseek-v4-flash|prompt:2.3.1|schema:2.0",
        help="选择覆盖全部JD的抽取器版本（默认P0-2正式版本2.3.1）",
    )
    parser.add_argument(
        "--target-size",
        type=int,
        default=75,
        help="预检输入实例数",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CONSOLIDATION_MAPPING_CHUNK_SIZE,
        help="映射分块上限（诊断截断时调小）",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="每个阶段的有限重试次数",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/P0-4/precheck-2.1.json"),
        help="脱敏指标报告输出路径",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=Path("data/private/experiments/P0-4/precheck-2.1-result.json"),
        help="完整归并结果（含证据）输出路径",
    )
    args = parser.parse_args()

    if not args.execute:
        print("必须显式--execute确认付费模型调用；本次未执行。")
        return 2

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1
    cases = load_consolidation_cases(args.cases)

    engine = create_database_engine("sqlite:///data/jd_skill_insight.db")
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            selection = load_consolidation_selection(
                session, extractor_version=args.extractor_version
            )
    finally:
        engine.dispose()

    precheck_input = build_precheck_input(selection, cases, args.target_size)
    mapping_requests = -(-len(precheck_input.occurrences) // args.chunk_size)
    print(f"模型：{settings.model}")
    print(f"抽取器版本：{selection.extractor_version}")
    print(f"输入范围：{len(precheck_input.occurrences)}条实例"
          f"（参考标注{sum(1 for _ in cases['expected']['mappings'])}条全覆盖）")
    print(f"预计模型调用：1（标准项）+ {mapping_requests}（映射块）+ 1（关系）"
          f" = {mapping_requests + 2} 次")
    print("输出目标：仅统计指标（脱敏）；完整结果写入私有目录。")

    client = RecordingClient(
        OpenAICompatibleConsolidationClient(settings)
    )
    metadata = ConsolidatorMetadata(model_name=settings.model)
    print(f"归并器版本：{metadata.consolidator_version}")
    try:
        result, raw = consolidate_with_correction(
            precheck_input,
            client,
            max_attempts=args.max_attempts,
            mapping_chunk_size=args.chunk_size,
        )
    except (ConsolidationError, ValueError) as exc:
        print(f"预检失败：{exc}")
        diagnose_path = Path("data/private/experiments/P0-4/precheck-diagnose.json")
        diagnose_path.parent.mkdir(parents=True, exist_ok=True)
        diagnose_path.write_text(
            json.dumps(client.records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"请求诊断记录（含响应开头）：{diagnose_path}")
        return 1

    metrics = evaluate_consolidation(result, cases)
    report = {
        "consolidator_version": metadata.consolidator_version,
        "extractor_version": selection.extractor_version,
        "input_fingerprint": selection.input_fingerprint,
        "input_size": len(precheck_input.occurrences),
        "annotated_size": len(cases["expected"]["mappings"]),
        "mapping_chunk_size": CONSOLIDATION_MAPPING_CHUNK_SIZE,
        "stage_requests": [
            {key: value for key, value in record.items() if key != "response_head"}
            for record in client.records
        ],
        "canonical_count": len(result.canonical_requirements),
        "mapping_count": len(result.mappings),
        "relation_count": len(result.relations),
        "metrics": {
            "mapping_accuracy": metrics.mapping_accuracy,
            "relation_precision": metrics.relation_precision,
            "relation_recall": metrics.relation_recall,
            "relation_f1": metrics.relation_f1,
            "mapping_matched": metrics.mapping_matched,
            "mapping_total": metrics.mapping_total,
            "relation_matched": metrics.relation_matched,
            "relation_predicted": metrics.relation_predicted,
            "relation_total": metrics.relation_total,
        },
    }

    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"参考映射准确率：{metrics.mapping_matched}/{metrics.mapping_total}"
          f" = {metrics.mapping_accuracy}")
    print(f"参考关系 P/R/F1：{metrics.relation_precision} / "
          f"{metrics.relation_recall} / {metrics.relation_f1}")
    print(f"全图：标准项{len(result.canonical_requirements)}、"
          f"映射{len(result.mappings)}、关系{len(result.relations)}")
    print(f"阶段请求记录：{json.dumps(client.records, ensure_ascii=False)}")
    print(f"脱敏报告：{args.report}")
    print(f"原始结果：{args.raw_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
