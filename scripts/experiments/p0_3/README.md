# P0-3 验收与实验脚本

本目录保存 P0-3 验收双轨道（Track A 合成规则场景 / Track B 真实 JD）与人工规则审计材料；两段式实现位于`app/extraction_two_stage.py`。当前唯一抽取配置：v0.8 + Schema V3（三级熟练度）。旧运行入口（`run_two_stage_extraction.py`、`evaluate_two_stage_results.py`）与旧评测方法已移除，历史由 Git 保存。

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 标记P0-3实验包，导入时不执行操作。 |
| `run_acceptance.py` | **Track A（合成规则场景）**：合同 + 变形性质 + 多次运行稳定性 + 三级熟练度；`--phase pilot\|acceptance`、`--execute` 完整验收 / `--dry-run` 预检。 |
| `run_real_jd_acceptance.py` | **Track B（真实 JD）**：显式选择 JD、每份 JD 独立运行 ≥3 次、合同 hard gate、稳定性与 proficiency distribution、与旧版本 diagnostic 对比、脱敏审计样本索引。 |

## 阶段分层（pilot / acceptance）

```text
pilot:      检查流程、收集指标（脚本只算自动 hard gate：passed）
acceptance: 使用已冻结的规则、范围与阈值；decision_eligible 恒为 False
```

**批准须人工汇总确认（DEC-018）**：脚本只计算自动 hard gate（`passed`）；
`decision_eligible` 恒为 False。最终批准由人工汇总记录
（`reports/templates/final-review.md`：Track A passed + Track B passed +
human audit completed + threshold decision recorded）确认后输出。

报告至少记录：`phase`、`prompt_version`、`schema_version`、`protocol_version`、scenario fingerprint（Track A）/ JD set fingerprint（Track B）、`runs`、hard gate failures、`decision_eligible`。

**轻量运行建议（DEC-018）**：Track A 全场景各跑 base+transformed 一次收集
规则基线；随机稳定性只挑 3～4 个高风险场景用 `--scenarios` 子集各重复
3 次；真实 JD（Track B）5 份各重复 2～3 次。

## Track A：合成规则场景验收（run_acceptance.py）

```powershell
# 预检（不调用模型，返回 0 = 预检通过，不是验收）
python -m scripts.experiments.p0_3.run_acceptance --dry-run

# 未指定执行模式：返回 2 并提示，不调用模型
python -m scripts.experiments.p0_3.run_acceptance

# Pilot（真实模型，检查流程与收集指标；不产生批准结论）
python -m scripts.experiments.p0_3.run_acceptance --execute --phase pilot

# Acceptance（使用已冻结的规则/范围/阈值；decision_eligible 恒 False，
# 最终批准由 final-review.md 人工汇总确认）
python -m scripts.experiments.p0_3.run_acceptance --execute --phase acceptance `
  --run-tag acceptance-v08-run1
```

- 规则场景默认读取 `data/rule_scenarios/extraction_metamorphic_cases.json`（protocol 1.1，13 个领域中性场景，不保存完整 expected extraction），可用 `--scenarios` 覆盖。
- 参数门禁：`--runs >= 3`、`--max-attempts >= 1`，非法参数在模型调用前失败。
- base_input 独立运行 `--runs` 次（每次完整合同检查），变形输入运行 1 次（同样完整合同检查）；运行完整性（expected/successful/failed）任何缺失都是 hard gate。
- 变形比较使用 `TransformationResult` 锚点（base→transformed 一对多映射 + 预期变化区域）；场景期望属性可定位到目标锚点（basic→advanced、basic→unknown、standalone→any_of、raw_name 跟随替换）。
- 多次运行 agreement 第一版只作 warning 报告实际基线，阈值待用户裁决后冻结。
- 脱敏报告默认写入 `reports/P0-3/<run-tag>-report.json`（不含完整 JD 文本）；原始结果写入 `data/private/experiments/p0_3/`。
- 不读取人工完整答案决定通过或失败。返回码：参数错误非零；dry-run 0（预检）；验收 hard gate 失败 1、通过 0。

## Track B：真实 JD 验收（run_real_jd_acceptance.py）

```powershell
# 预检（不调用模型）
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --all --dry-run

# Pilot（真实模型）
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --job-ids 1 2 3 --execute --phase pilot

# Acceptance（已冻结规则/范围/阈值）
python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --all --execute --phase acceptance `
  --run-tag real-jd-v08-run1
```

- 范围：`--all` 与 `--job-ids` 互斥必选；数据库目标 `--use-project-database` 与 `--database-url` 必选其一。
- 每份 JD 独立运行 ≥3 次；每次合同 hard gate 全通过；报告块对齐、项数漂移、字段一致性、组成员一致性、evidence attribution、proficiency distribution（basic/advanced/unknown）。
- 与旧版本只做 diagnostic 对比（requirement 总数变化、三级分布、P0-4 输入实例数量变化——只读统计，不自动执行 P0-4 归并）。
- 输出供人工规则审计的脱敏样本索引（`audit_samples`，不含原文）；真实原文与完整模型响应只写入 `data/private/experiments/p0_3/real_jd/`。

## 人工规则审计

审计记录模板：`reports/templates/extraction-rule-audit.json`（字段：`audit_id`/`extractor_version`/`scenario_or_job_id`/`rule_id`/`violation`/`severity`/`evidence_reference`/`reason`/`recommended_action`/`reviewer`/`reviewed_at`；模板不包含真实 JD，`evidence_reference` 只引用私有输出位置）。

- 版本：当前唯一配置 v0.8 + Schema V3（`app/extraction.py` 的 `PROMPT_VERSION`/`SCHEMA_VERSION`，与 `app/extraction_two_stage.py` 的 `TWO_STAGE_PROMPT_VERSION`/`TWO_STAGE_SCHEMA_VERSION` 同步，测试锁定）。
