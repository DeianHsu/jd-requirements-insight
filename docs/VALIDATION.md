# 证据、覆盖与抽取/归并验证协议

> 只在处理证据、规则场景、变形测试、多次运行稳定性或人工规则审计时读取本文。
> 规则 ID：`EVID-01`～`EVID-04`、`COVER-01`～`COVER-04`。

## 1. 证据规则（EVID）

每条证据必须：

1. 是 JD 中的连续原文（`EVID-01`）；
2. 足以支持要求名称（`EVID-02`）；
3. 足以支持重要程度、熟练度和年限判断（`EVID-02`）；
4. 尽量是最短但信息完整的片段（`EVID-01`）；
5. 不得改写、拼接或拼接不连续文本（`EVID-01`）。

多个原子项可以共享同一句证据（`EVID-03`）。

```text
熟悉LangChain，有RAG项目经验者优先。
```

推荐：

```text
LangChain → 熟悉LangChain
RAG       → 有RAG项目经验者优先
```

只引用 `RAG` 不足以支持 `preferred` 判断。

**证据存在性 ≠ 证据支持性（`EVID-04`）**：自动校验只能确认证据文本存在于
原文（证据存在性）；证据是否确实足以支持名称和字段判断（证据支持性）仍需
人工复核。

## 2. 覆盖规则（COVER）

- `COVER-01`：发现段每个分句都必须出现在某个候选块的 sentence_indexes 中；
- `COVER-02`：同一分句不得重复归属到多个候选块；
- `COVER-03`：source_span 必须是原文连续片段，与分句索引一致；
- `COVER-04`：判断段必须处理所有非 excluded 候选块；requirement 块必须
  产出 requirement；**responsibility 块不得产出 requirement**（职责不得
  误抽为候选人要求）；excluded 块不得产出 must/preferred requirement。

## 3. 验证材料分类

| 类别 | 是什么 | 地位 | 存放位置 |
|---|---|---|---|
| 确定性测试 expected output | 测试解析器、Schema、证据校验等确定性代码时使用的完整精确期望输出 | 正式（仅限确定性代码） | `tests/` 内嵌或 fixture |
| 规则场景 | 领域中性的基础输入 + 确定性变换 + 期望属性（不保存完整 expected extraction）；变换返回锚点映射与变化区域 | 正式 | `data/rule_scenarios/` |
| 变形测试 | 对同一基础输入应用确定性变换后比较两次抽取，以 TransformationResult 锚点对齐（支持一句拆两句的一对多映射与重复句 occurrence） | 正式（hard gate 依据） | `data/rule_scenarios/` + 验证脚本 |
| 多次运行稳定性 | 相同输入独立运行多次，以候选块为锚点报告块对齐与漂移；只作 warning | 正式（warning） | 验证脚本 |
| 人工规则审计 | 人工按规则 ID 检查输出是否违反规则、证据是否支持结论，记录 `rule_id`/`violation`/`severity`/`evidence_reference`/`reason`/`recommended_action` | 正式（人工职责） | 审计报告 |

## 4. 抽取 acceptance

- **规则场景 acceptance**（`scripts/experiments/p0_3/run_acceptance.py`）：
  所有场景的 base 与 transformed 各运行一次；少量高风险场景可用
  `--scenarios` 子集与 `--runs` 显式重复；不建立复杂批准逻辑。
  报告保留：model、prompt_version、schema_version、scenario fingerprint、
  run count、contract failures、transformation failures、stability
  warnings、manual review notes。
- **真实 JD acceptance**（`scripts/experiments/p0_3/run_real_jd_acceptance.py`）：
  显式选择数据库（`--use-project-database` 或 `--database-url`）与 JD、
  每份 JD 支持重复运行、Schema/coverage/evidence/逻辑组合同检查、项目
  数量与字段漂移、异常项索引供人工复核。
  人工直接根据验证报告判断当前抽取方案是否可以进入下游，不开发额外
  批准系统。

## 5. 归并 acceptance

归并 acceptance 只处理 requirement instance → canonical requirement → unique mapping。
归并模型一次输出 canonical requirements 和来源实例分区（模型只负责决定
cluster）；mappings 由确定性代码从来源分区生成并持久化。
保留真正影响统计可信度的检查：

- coverage = 100%；
- 重复映射 = 0；
- 未知 canonical 引用 = 0；
- 空 canonical cluster = 0；
- positive-pair Jaccard；
- canonical 数量漂移；
- singleton 比例漂移；
- 输入顺序变形；
- 人工检查所有多成员 cluster。

输出结构：input fingerprint、run count、coverage、structural failures、
positive-pair Jaccard、canonical count range、singleton ratio range、
order transformation result、manual cluster review notes。顺序变形的
合同违规（coverage/结构违规）与聚类失败计入 hard gate；jaccard 低于
阈值只作 warning。

## 6. 当前合同适用范围

仅接受 v0.10 + Schema V3 与现行数据库结构；其他版本或结构明确拒绝，不迁移、
不兼容。
