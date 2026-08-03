# JD 语义决策规则索引

> 规范版本：1.5
> 适用范围：JD 职责、岗位要求、原文证据与抽取验证协议
> 方法依据：`docs/DECISIONS.md` DEC-015（抽取层取消完整人工 Gold，改为规则化验证 + 人工违规审计）

本目录是 JD 语义决策规则和数据语义的唯一完整规范。处理任务时只读取与当前目标对应的主题文档，不默认加载全部规范。

## 主题路由

| 当前任务 | 必读文档 |
|---|---|
| 标注或检查岗位职责 | [RESPONSIBILITIES.md](RESPONSIBILITIES.md) |
| 标注原子要求、类别、重要程度、熟练度或年限 | [REQUIREMENTS.md](REQUIREMENTS.md) |
| 处理证据、规则场景、变形测试、稳定性或人工违规审计 | [DATASET_EVALUATION.md](DATASET_EVALUATION.md) |

## 规则 ID 总表

规则 ID 稳定、唯一，可被 Prompt、测试和审计报告引用。修改规则语义必须同步更新本表、对应条文、Prompt 引用和测试；规则 ID 一旦发布不得复用或改指其他规则。

| 规则组 | 含义 | 规则 |
|---|---|---|
| `RESP-*` | 职责边界、拆分、合并、实施方式与示例 | RESP-01～RESP-07 |
| `REQ-*` | 要求边界、原子化、禁止推断与点名技术 | REQ-01～REQ-08 |
| `GROUP-*` | 任选逻辑组建立与结构 | GROUP-01～GROUP-03 |
| `FIELD-*` | category、importance、proficiency、年限与未知字段 | FIELD-01～FIELD-05 |
| `EVID-*` | 证据连续、最小、支持性与共享 | EVID-01～EVID-04 |
| `COVER-*` | 分句覆盖、唯一覆盖、span 对应与判断段覆盖 | COVER-01～COVER-05 |

## 共同原则

1. 只依据 JD 明示内容，不补充行业常识或隐含技能（`REQ-06`）。
2. 每条职责和要求只表达一个符合对应粒度规则的原子事实（`RESP-02`、`REQ-02`）。
3. 每条结果绑定能够支持结论的连续原文证据（`EVID-01`、`EVID-02`）。
4. 要求实例与后续标准要求项分层保存，抽取阶段不提前归并（`REQ-05`）。
5. 不确定的字段使用 `unknown` 或 `null`，不能猜测（`FIELD-05`）。
6. 没有唯一 Gold 不等于任何输出都合法：合同违规、无依据事实、遗漏、重复、非法逻辑组、明显不稳定和下游破坏仍然必须被发现。

## 人工角色（DEC-015）

人工不再为每条 JD 提供唯一完整 JSON 答案，也不再以人工答案 F1、期望数量或字段准确率作为正式 Prompt 验收门槛。人工职责为：

1. 检查规则是否合理（规则制定者）；
2. 审计模型输出是否违反规则（违规审计者）；
3. 检查证据支持性（`EVID-04`，自动存在性检查不能替代）；
4. 记录风险类型和严重度；
5. 决定应修改规则、Prompt、代码，还是接受合理差异。

人工审计记录格式（本轮不新增数据库表，先以报告形式记录）：

| 字段 | 含义 |
|---|---|
| `rule_id` | 被违反或被检查的规则 ID |
| `violation` | 违规描述 |
| `severity` | 严重度（如 high / medium / low） |
| `evidence` | 定位到原文或输出的证据 |
| `reason` | 判断理由 |
| `recommended_action` | 建议动作（改规则 / 改 Prompt / 改代码 / 接受） |

旧 `annotation_cases.json`、F1 报告和基于 Gold 的审批历史全部保留，但降级为 legacy protocol 历史材料：命令仍可复现历史结果，但不得用于批准新的 Prompt。

## 核心规则与领域配置

- 原子化（`REQ-02`）、覆盖（`COVER-*`）、证据（`EVID-*`）、逻辑组（`GROUP-*`）是领域无关核心规则。
- 当前 category 枚举（`app/schemas.py` 的 `RequirementCategory`）是 AI/LLM/Agent/RAG 岗位领域配置，不是核心规则。
- 本项目当前不扩展到其他职业；不得声称现有 category 枚举可直接用于任意领域。
- 不要修改 category Schema；扩展领域时重新评估枚举与规则适用性。

## 当前数据合同

要求项由抽取数据合同 V2 保存以下核心字段：

| 字段 | 含义 | 规则 |
|---|---|---|
| `raw_name` | JD 中的原始要求名称，不做同义归并 | `REQ-05` |
| `category` | 要求类别（领域配置枚举） | `FIELD-01` |
| `importance` | `must`、`preferred`、`mentioned` 或 `unknown` | `FIELD-02` |
| `proficiency` | 原文明示的掌握程度 | `FIELD-03` |
| `group_id` / `group_logic` | 独立要求或 `any_of` 任选组 | `GROUP-02` |
| `min_years` / `max_years` / `years_text` | 年限下限、原文上限和完整表达 | `FIELD-04` |
| `evidence` | 支持结论的连续 JD 原文 | `EVID-01`、`EVID-02` |
| `confidence` | 抽取置信度；不再把人工确认等同于 `1.0` | — |

标准要求项、要求关系和映射理由属于后续跨 JD 原子要求归并阶段，不加入当前抽取数据合同；具体字段名以未来稳定代码合同为准。
