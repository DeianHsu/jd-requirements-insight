# JD Requirements Insight

把非结构化招聘 JD 转化为**可统计、可审核、可追溯原文证据的岗位要求市场报告**。
项目重点是用自动合同门禁、明确身份、唯一映射和可复现统计口径约束随机 LLM 输出，
让使用者把一组 JD 放进目录后用一条命令得到报告。

[查看公开合成报告](examples/market-report-sample.md) ·
[查看当前状态](docs/CURRENT_STATE.md) ·
[查看架构](docs/ARCHITECTURE.md)

## v0.1 状态

v0.1 已完成并冻结，正式链已经在本地私有数据集上完成端到端验收。真实 JD、公司信息、
模型原始响应、人工裁决文件和市场结论不提交 Git。

公开仓库保留完整代码、自动化测试和虚构 sample，用于说明系统如何约束模型输出、验证
正式化身份、计算统计并渲染报告；它们不声称复现私人数据结论。

## v0.1 匿名验收摘要

v0.1 已在 15 份真实 JD 上完成私有端到端验收：

- 409 requirement instances；
- 329 canonical requirements；
- consolidation coverage 100%；
- structural violations 0；
- 最终结果经过人工裁决并满足报告门禁。

这些数字只证明当前工程闭环已经在真实数据上运行，不构成岗位市场结论。真实 JD、原始
模型响应、人工裁决内容和具体市场发现仍保持私有。

## 一键使用

需要 Python 3.11、[uv](https://docs.astral.sh/uv/) 和 `.env` 中的 LLM 配置：

```powershell
uv sync
uv run python -m app.cli analyze-jds data/raw_jds --execute
```

`--execute` 是付费调用的显式确认。命令自动导入目录中的 Markdown JD，并在
`data/private/runs/<输入指纹>/` 创建隔离数据库、运行摘要、抽取/归并 artifact 和最终
Markdown 报告。同一输入已完整成功时直接复用，不重复调用模型；可用 `--output <path>`
另存报告。

## 当前 Pipeline

```text
Markdown JD 目录
→ 两段式抽取（有限纠错 + 证据/覆盖/Schema 硬门）
→ 自动正式化 requirement instances
→ 单次保守归并（有限纠错 + 精确覆盖/结构硬门）
→ 自动正式化 canonical requirements 与唯一 mappings
→ 独立 JD 统计 + 原文 evidence 追溯
→ 确定性 Markdown 市场报告
```

自动策略只在当前硬门全部通过时正式化；任一 JD 抽取失败时整批抽取不写入，归并失败时
不生成正式归并批次或报告。无法安全合并的要求由模型按 Prompt 保持 singleton。

## 可选单次预检

`extract-jds --candidate-output` 与 `consolidate-requirements --candidate-output`
各发起一次模型运行并写私有 JSON，适合在完整验收前快速观察输出。它们可以跳过：

- candidate 不是 acceptance 产物；
- candidate 不作为 finalize 输入；
- candidate 不写正式抽取或归并表。

## 核心设计

- **事实层与标准层分离**：requirement instance 保留原文 evidence；canonical
  requirement 只负责跨 JD 统计，二者通过唯一 mapping 连接。
- **生成与正式数据隔离**：模型结果通过当前合同硬门后，系统策略才调用共用 finalize
  写入核心；模型调用本身不直接写正式表。
- **评测不是单一准确率**：抽取检查证据、覆盖、规则变形和漂移；归并检查 exact
  coverage、结构违规、positive-pair Jaccard、顺序变形和稳定性。
- **自动批准可追溯**：策略版本、批准 run、输入/结果指纹和最终结果均绑定到正式记录。
- **统计口径可复算**：频率按独立 JD 数计算，同一 JD 的重复实例只贡献一次覆盖；报告
  逐项回链原始 requirement 与 evidence。

## 公开 Sample

```powershell
uv run python -m scripts.make_sample_report
```

sample 使用虚构公司、岗位和要求，在临时 SQLite 数据库中调用正式统计与报告渲染代码。
它不需要 `.env`、不会调用 LLM、不会读取私有数据。

验证重新生成结果一致：

```powershell
uv run python -m scripts.make_sample_report
git diff --exit-code -- examples/market-report-sample.md
```

## 报告阅读方式

报告包含完整的出现频率榜和准备优先级榜，展示独立 JD 篇数、占比及必需、加分、普通
提及、未明确的篇数。准备优先级按必需篇数、加分篇数、总出现篇数依次降序。任选条件
单独标注，来源与原文证据在末尾折叠展示，指纹和模型版本保留在运行数据中。

已有 Markdown 不会随代码更新自动变化，可通过离线 `generate-report` 对明确指定的
数据库及归并批次重新生成，不需要再次调用模型。

## 开发者验证工具

多次运行 acceptance、稳定性分析、人工规则/cluster 审计、candidate 和文件型
`finalize-extraction` / `finalize-consolidation` 仍然保留，用于修改模型、Prompt、Schema
或规则后的离线评测，不是使用者每批 JD 的必经路径。完整导航和参数边界见
[scripts/README.md](scripts/README.md)。

## 验证

```powershell
uv run python -m app.cli --help
uv run pytest
uv run ruff check app scripts tests
```

自动化测试使用 fake 客户端、临时文件和临时数据库，不调用付费服务。

## 数据、安全与限制

- 真实 JD、数据库、密钥和原始模型响应属于私有材料，不提交 Git；
- 当前只支持 v0.10 + Schema V3 和现行数据库结构，不兼容或迁移旧派生数据；
- 一键主线只自动保证 Schema、覆盖、证据存在性、精确映射和身份等机器合同；LLM 的
  语义判断仍可能出错，输出属于 MVP 分析结果，不等同于人工审计结论；
- 若既有正式抽取缺少现行机器可验证 provenance，报告只在私有、范围受限的结构化
  waiver 明确覆盖时放行，并必须披露风险；新增数据不得继承例外；
- 匿名工程验收摘要可以公开，但市场结论和私有批次明细不能从公开 clone 逐字复算；
- 项目不提供 Web UI、在线服务、简历匹配、ATS 或自动投递 Agent。

## 文档导航

| 文档 | 职责 |
|---|---|
| [CURRENT_STATE](docs/CURRENT_STATE.md) | 当前软件基线、安全门和公开/私有边界 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 一键主线、开发者验证链、模块边界和设计理由 |
| [GLOSSARY](docs/GLOSSARY.md) | 核心业务术语与流水线不变量 |
| [EXTRACTION_RULES](docs/EXTRACTION_RULES.md) | 职责边界、岗位要求、逻辑组和字段规则 |
| [VALIDATION](docs/VALIDATION.md) | 证据、覆盖、抽取与归并验收合同 |
