# P0-3 两段式实验脚本

本目录保存两段式抽取的真实调用、离线评测（legacy）与新协议验收（Track A / Track B）；两段式实现本身位于`app/extraction_two_stage.py`。

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 标记P0-3实验包，导入时不执行操作。 |
| `run_two_stage_extraction.py` | 显式选择数据库并调用真实LLM，把原始结果写入私有实验目录。 |
| `evaluate_two_stage_results.py` | **legacy**（DEC-015）：离线读取私有标注与实验结果，生成分层指标报告，不调用LLM；仅用于历史比较和案例分析，不属于当前正式验收，不得用于批准新的 Prompt。 |
| `run_acceptance.py` | **Track A（合成规则场景）**：合同 + 变形性质 + 多次运行稳定性 + 三级熟练度；显式使用 candidate v0.8 + Schema V3；`--execute` 完整验收 / `--dry-run` 预检。 |
| `run_real_jd_acceptance.py` | **Track B（真实 JD）**：显式选择 JD、每份 JD 独立运行 ≥3 次、合同 hard gate、稳定性与 proficiency distribution、与 v0.6 diagnostic 对比、脱敏审计样本索引；`--execute` / `--dry-run`。 |

## Track A：合成规则场景验收（run_acceptance.py）

```powershell
# 预检（不调用模型，返回 0 = 预检通过，不是验收）
python -m scripts.experiments.p0_3.run_acceptance --dry-run

# 未指定执行模式：返回 2 并提示，不调用模型
python -m scripts.experiments.p0_3.run_acceptance

# 真实模型验收（必须显式--execute，会产生费用）
python -m scripts.experiments.p0_3.run_acceptance --execute

# 指定运行次数与运行标识（独立文件，不覆盖历史）
python -m scripts.experiments.p0_3.run_acceptance --execute --runs 3 `
  --run-tag acceptance-v08-run1
```

- 规则场景默认读取 `data/rule_scenarios/extraction_metamorphic_cases.json`（protocol 1.1，13 个领域中性场景，不保存完整 expected extraction），可用 `--scenarios` 覆盖。
- 参数门禁：`--runs >= 3`、`--max-attempts >= 1`，非法参数在模型调用前失败。
- base_input 独立运行 `--runs` 次（每次完整合同检查），变形输入运行 1 次（同样完整合同检查）；运行完整性（expected/successful/failed）任何缺失都是 hard gate。
- 变形比较使用 `TransformationResult` 锚点（base→transformed 一对多映射 + 预期变化区域）；场景期望属性可定位到目标锚点（basic→advanced、basic→unknown、standalone→any_of、raw_name 跟随替换）。
- 多次运行 agreement 第一版只作 warning 报告实际基线，阈值待用户裁决。
- 脱敏报告默认写入 `reports/P0-3/<run-tag>-report.json`（含 model、prompt version、schema version、scenario_set_fingerprint、runs、max_attempts、run identifier、timestamp，不含完整 JD 文本）；原始结果写入 `data/private/experiments/p0_3/`。
- 不读取人工完整答案决定通过或失败。返回码：参数错误非零；dry-run 0（预检）；验收 hard gate 失败 1、通过 0。

## Track B：真实 JD 验收（run_real_jd_acceptance.py）

```powershell
# 预检（不调用模型）
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --all --dry-run

# 真实模型验收（必须显式--execute）
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --job-ids 1 2 3 --execute

# 验收全部 JD，指定运行次数
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --all --execute --runs 3 --run-tag real-jd-v08-run1
```

- 范围：`--all` 与 `--job-ids` 互斥必选；数据库目标 `--use-project-database` 与 `--database-url` 必选其一。
- 每份 JD 独立运行 ≥3 次；每次合同 hard gate 全通过；报告块对齐、项数漂移、字段一致性、组成员一致性、evidence attribution、proficiency distribution（basic/advanced/unknown）。
- 与 v0.6 只做 diagnostic 对比（requirement 总数变化、三级分布、P0-4 输入实例数量变化——只读统计，不自动执行 P0-4 付费归并）。
- 输出供人工规则审计的脱敏样本索引（`audit_samples`，不含原文）；真实原文与完整模型响应只写入 `data/private/experiments/p0_3/real_jd/`。

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
- 版本：active v0.6 + Schema V2（`app/extraction.py` 的 `PROMPT_VERSION`/`SCHEMA_VERSION`）；candidate v0.8 + Schema V3（`CANDIDATE_EXTRACTION_PROFILE`）；v0.7 为历史未批准候选版本（Git 历史承担复现）。
