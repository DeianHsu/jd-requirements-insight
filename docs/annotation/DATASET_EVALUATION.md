# 证据、规则场景与抽取验证协议

> 只在处理证据、规则场景、变形测试、多次运行稳定性或人工违规审计时读取本文。
> 方法依据：`docs/DECISIONS.md` DEC-015（抽取层取消完整人工 Gold）。

## 1. 证据规则（EVID）

每条证据必须：

1. 是 JD 中的连续原文（`EVID-01`）；
2. 足以支持职责或要求名称（`EVID-02`）；
3. 足以支持重要程度、熟练度和年限判断（`EVID-02`）；
4. 尽量是最短但信息完整的片段（`EVID-01`）；
5. 不得改写、概括或拼接不连续文本（`EVID-01`）。

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

**证据存在性 ≠ 证据支持性（`EVID-04`）**：自动校验只能确认证据文本存在于原文（证据存在性）；证据是否确实足以支持名称和字段判断（证据支持性）仍需人工复核。

## 2. 抽取验证协议总览

验证材料分为六类，用途和地位各不相同：

| 类别 | 是什么 | 地位 | 存放位置 |
|---|---|---|---|
| legacy Gold 数据 | 旧人工完整标注（`annotation_cases.json`）与 F1 报告 | 历史材料，不属于当前正式验收；不得用于批准新 Prompt | `data/private/`（本地私有） |
| 确定性测试 expected output | 测试解析器、Schema、证据校验等确定性代码时使用的完整精确期望输出 | 正式（仅限确定性代码） | `tests/` 内嵌或 fixture |
| 规则场景 | 领域中性的基础输入 + 确定性变换 + 期望属性（不保存完整 expected extraction）；protocol 1.1 起变换返回锚点映射与变化区域 | 正式 | `data/rule_scenarios/` |
| 变形测试 | 对同一基础输入应用确定性变换后比较两次抽取，以 TransformationResult 锚点对齐（支持一句拆两句的一对多映射与重复句 occurrence） | 正式（hard gate 依据） | `data/rule_scenarios/` + 验收脚本 |
| 多次运行稳定性 | 相同输入独立运行 ≥3 次，比较候选块与原子事实漂移（对齐优先级：sentence_indexes → span occurrence → kind → 覆盖集合） | 正式（第一版为 warning，阈值待用户裁决） | 验收脚本 |
| 人工违规审计 | 按规则 ID 检查输出是否违规、证据是否支持结论；模板 `reports/templates/extraction-rule-audit.json` | 正式（人工职责） | 审计模板 + 审计报告 |

**只有测试解析器、Schema、证据校验等确定性代码时，才允许完整精确 expected output。** 规则场景不得保存完整 expected extraction，模型验收不读取人工完整答案决定通过或失败。

## 3. 人工违规审计

人工审计不再对照唯一完整 JSON 答案，而是按规则 ID 检查输出是否违反规则（规则见 [README.md](README.md) 规则 ID 总表）。审计记录格式：

| 字段 | 含义 |
|---|---|
| `rule_id` | 被违反或被检查的规则 ID |
| `violation` | 违规描述 |
| `severity` | 严重度（如 high / medium / low） |
| `evidence` | 定位到原文或输出的证据 |
| `reason` | 判断理由 |
| `recommended_action` | 建议动作（改规则 / 改 Prompt / 改代码 / 接受合理差异） |

人工职责：检查规则是否合理、审计是否违反规则、检查证据支持性、记录风险类型和严重度、决定应修改规则、Prompt、代码还是接受合理差异。不得因为模型输出与某个人工答案不同就修改 Prompt。

## 4. 规则场景与变形测试

规则场景文件（`data/rule_scenarios/extraction_metamorphic_cases.json`）每个场景至少包含：

| 字段 | 含义 |
|---|---|
| `scenario_id` | 稳定场景 ID |
| `rule_ids` | 场景检查的规则 ID 列表 |
| `base_input` | 领域中性基础输入（合成 JD 文本，不含真实 JD 内容） |
| `transformation` | 确定性文本变换（如格式变化、加无关段落、措辞变化） |
| `expected_properties` | 变换后必须保持的属性（如事实集保留、字段不变性） |
| `forbidden_violations` | 禁止出现的违规（引用规则 ID，人工审计复核） |
| `severity` | 场景严重度 |

场景类别至少覆盖：

1. 项目符号、编号、空格和换行变化（格式不变性）；
2. 互不相关段落顺序变化（顺序不变性）；
3. 增加福利、地址、公司介绍等无关内容（无关输入隔离）；
4. 精确重复条件（不产生新事实）；
5. “熟悉”改为“精通”（显式字段变化只影响目标字段）；
6. 普通要求改为“优先”（`FIELD-02`）；
7. “和”改为明确“至少一种”（`GROUP-01`）；
8. Python、LangChain 等替换为技术甲、框架乙（改名不变性）；
9. 将一句拆成两句（原子事实不丢失）；
10. 加入相关但没有明确要求的技术，禁止补充成条件（`REQ-06`）。

三种使用方式：

- **deterministic fixture**：单元测试用假客户端或手工构造结果验证检查器逻辑，不调用真实模型；
- **model metamorphic experiment**：真实验收（`--execute`）对基础输入与变换输入各抽取一次并比较；
- **human audit scenario**：人工按 `forbidden_violations` 复核输出是否违反规则。

## 5. 多次运行稳定性

相同输入（固定模型、Prompt 版本、Schema 版本、输入指纹、temperature）独立运行至少 3 次，以候选块为锚点比较（相同输入对齐优先级：sentence_indexes → 规范化 source_span（含 occurrence，重复句不互相覆盖）→ kind → 分句覆盖集合）：

- discovery kind agreement；
- candidate block alignment rate；
- atomic item count agreement；
- evidence span agreement；
- category / importance / proficiency（三级）/ group type / group membership agreement；
- unmatched item count 与 drifted block identifiers。

第一版多次运行 agreement 作为 warning 报告实际基线，不预设迎合当前模型的阈值；阈值待用户裁决后生效。

## 6. 抽取验收分级与双轨道

验收报告分为三档：

| 档位 | 含义 | 示例 |
|---|---|---|
| hard gates | 必须全部通过 | Schema 违规 0、证据违规 0、discovery 覆盖 100%、重复覆盖 0、judge 候选类型覆盖 100%（responsibility/requirement/mixed 类型严格对应，excluded 块不得产出 must/preferred）、非法逻辑组 0、无依据明确事实 0（evidence_unattributed_items）、版本身份完整、运行完整性（expected/successful/failed 任何缺失）、格式变换无实质事实丢失、无关输入不改变原有候选块和原子事实 |
| warnings | 记录但不作否决 | 多次运行 agreement、数量漂移、名称漂移、ambiguous evidence |
| diagnostics | 仅供分析 | 名称相似度、临时 group_id 映射、单成员 any_of、跨组合并、逐场景明细 |

验收双轨道（各自独立，互不替代）：

- **Track A（合成规则场景）**：`scripts/experiments/p0_3/run_acceptance.py`——合同、变形性质、多次运行稳定性、三级熟练度、规则迁移；
- **Track B（真实 JD）**：`scripts/experiments/p0_3/run_real_jd_acceptance.py`——显式选择 JD、候选 profile v0.8 + Schema V3、每份 JD 独立运行 ≥3 次，报告块对齐/项数漂移/字段一致性/组成员一致性/evidence attribution/proficiency distribution，与 v0.6 只做 diagnostic 对比（不自动执行 P0-4 归并），输出脱敏审计样本索引。

两个 Track 都通过并完成人工审计后，才能讨论 v0.8 替换 v0.6。

名称相似度只能作为 diagnostic，不得成为唯一 hard gate；不得调用另一个 LLM 作为比较器。验收报告必须记录 model、prompt version、schema version、scenario_set_fingerprint、runs、max_attempts、input fingerprint、run identifier 和 timestamp，且不得输出完整私有 JD。人工审计记录使用 `reports/templates/extraction-rule-audit.json` 模板（`audit_id`/`extractor_version`/`scenario_or_job_id`/`rule_id`/`violation`/`severity`/`evidence_reference`/`reason`/`recommended_action`/`reviewer`/`reviewed_at`；模板不包含真实 JD，`evidence_reference` 只引用私有输出位置）。

## 7. legacy Gold 数据

- 旧 `annotation_cases.json`（development/regression/validation 分组）标记为 `protocol = legacy_gold`、`status = historical`、`official_gate = false`；
- 旧 `evaluate-cases` 命令和相关实验脚本标记为 legacy：命令仍可复现历史结果，但 CLI 帮助与文档明确其不属于当前正式验收、不得用于批准新的 Prompt，只用于历史比较和案例分析；
- 旧 F1 数字不得删除，统一标记为 legacy protocol 结果；
- 旧五级熟练度值经确定性映射读取（understand/familiar → basic、proficient/expert → advanced），物理数据不重写（DEC-016）。

## 8. 确定性代码的 expected output

对解析器、Schema 校验、证据校验、覆盖检查、运行间比较等确定性代码，单元测试必须使用完整精确的 expected output（如 `JobExtractionResult`、`DiscoveryResult` 的期望值），与规则场景（不保存完整 expected extraction）严格区分。
