# P0-3 两段式实验脚本

本目录保存两段式抽取的真实调用、离线评测（legacy）与新协议验收入口；两段式实现本身位于`app/extraction_two_stage.py`。

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 标记P0-3实验包，导入时不执行操作。 |
| `run_two_stage_extraction.py` | 显式选择数据库并调用真实LLM，把原始结果写入私有实验目录。 |
| `evaluate_two_stage_results.py` | **legacy**（DEC-015）：离线读取私有标注与实验结果，生成分层指标报告，不调用LLM；仅用于历史比较和案例分析，不属于当前正式验收，不得用于批准新的 Prompt。 |
| `run_acceptance.py` | **当前正式验收入口（新协议）**：规则场景 + 合同检查 + 多次运行稳定性 + 变形测试；默认不调用外部模型，`--execute` 显式授权；报告分级 hard gate / warning / diagnostic。 |

## 新协议验收（run_acceptance.py）

```powershell
# 默认模式：只加载规则场景并做确定性预检，不调用模型
python -m scripts.experiments.p0_3.run_acceptance

# 真实模型验收（必须显式--execute，会产生费用）
python -m scripts.experiments.p0_3.run_acceptance --execute

# 指定运行次数与运行标识（独立文件，不覆盖历史）
python -m scripts.experiments.p0_3.run_acceptance --execute --runs 3 `
  --run-tag acceptance-v07-run1
```

- 规则场景默认读取 `data/rule_scenarios/extraction_metamorphic_cases.json`（10 个领域中性场景，不保存完整 expected extraction），可用 `--scenarios` 覆盖。
- base_input 独立运行 `--runs` 次（默认 3），变形输入各运行 1 次；每次 base 运行都执行确定性合同检查（Schema、覆盖、证据、逻辑组、归属、身份）。
- 多次运行 agreement 第一版只作 warning 报告实际基线，阈值待用户裁决。
- 脱敏报告默认写入 `reports/P0-3/<run-tag>-report.json`（含 model、prompt version、schema version、input fingerprint、run identifier、timestamp，不含完整 JD 文本）；原始结果（含完整输入与模型响应）写入 `data/private/experiments/p0_3/`。
- 不读取人工完整答案决定通过或失败。

## 历史运行方式（legacy）

```powershell
# 使用项目数据库执行真实调用；必须显式确认execute
python -m scripts.experiments.p0_3.run_two_stage_extraction `
  --use-project-database --execute

# 只处理单份JD（可重复指定），用于单份先验证
python -m scripts.experiments.p0_3.run_two_stage_extraction `
  --use-project-database --job-id 1 --execute

# 离线评测（legacy），覆盖开发/回归/验证三个分组；仅历史比较
python -m scripts.experiments.p0_3.evaluate_two_stage_results

# 提供私有JD原文后才会统计证据存在率
python -m scripts.experiments.p0_3.evaluate_two_stage_results `
  --source-texts data/private/experiments/p0_3/jd_source_texts.json

# 评测指定验收轮次结果（不覆盖既有结果文件）
python -m scripts.experiments.p0_3.evaluate_two_stage_results `
  --results data/private/experiments/p0_3/two_stage_results_acceptance_run1.json `
  --output reports/experiments/p0_3/two_stage_evaluation_acceptance_run1.md
```

- 参数：`--use-project-database` 与 `--database-url` 必须二选一；`--job-id` 可重复、缺省为全部JD；真实调用必须 `--execute`。
- 评测参数：`--source-texts` 可选（私有`{source_file: 全文}`JSON，提供后统计证据存在率）；`--results`/`--output` 可指向验收轮次独立文件，避免覆盖历史结果。
- 报告按`development`/`regression`/`validation`三个分组输出分层指标与case_id级失败案例（不含私有名称内容）；证据存在率在未提供原文时显示0/0并注明未统计。
- 默认原始结果写入`data/private/experiments/p0_3/`，脚本当前生成的本地评测草稿写入`reports/experiments/p0_3/`。
- 脱敏后的P0-3详细分析统一整理到`reports/P0-3/`；当前两段式实验主报告为`reports/P0-3/two-stage-experiment.md`。
- 实验版本号位于`app/extraction_two_stage.py`的`TWO_STAGE_PROMPT_VERSION`（当前v0.7；v0.6 历史 Prompt 保留为 `LEGACY_*_V06` 常量）。
