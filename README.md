# JD Requirements Insight

把非结构化招聘 JD 转化为**可统计、可审核、可追溯原文证据的岗位要求市场报告**。
项目重点是把随机 LLM 输出收口成具有明确身份、唯一映射、人工审核记录和可复现统计口径
的正式数据产品。

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

## 正式 Pipeline

```text
Markdown JD 导入与去重
→ v0.10 + Schema V3 抽取 acceptance（多次运行 + 合同检查）
→ 人工审核 → finalize-extraction
→ requirement instance 归并 acceptance（多次运行 + 顺序变形 + 稳定性分析）
→ 人工 must-link / cannot-link / 名称裁决
→ finalize-consolidation
→ 独立 JD 统计 + 原文 evidence 追溯
→ 确定性 Markdown 市场报告
```

正式 finalize 只消费完整 acceptance report/raw 和对应人工审核记录。

## 可选单次预检

`extract-jds --candidate-output` 与 `consolidate-requirements --candidate-output`
各发起一次模型运行并写私有 JSON，适合在完整验收前快速观察输出。它们可以跳过：

- candidate 不是 acceptance 产物；
- candidate 不作为 finalize 输入；
- candidate 不写正式抽取或归并表。

## 核心设计

- **事实层与标准层分离**：requirement instance 保留原文 evidence；canonical
  requirement 只负责跨 JD 统计，二者通过唯一 mapping 连接。
- **验收与正式数据隔离**：多次运行、合同检查和人工审核完成后，finalize 才能原子写入
  模型生成的正式数据。
- **评测不是单一准确率**：抽取检查证据、覆盖、规则变形和漂移；归并检查 exact
  coverage、结构违规、positive-pair Jaccard、顺序变形和稳定性。
- **Human-in-the-loop 可追溯**：批准 run、review decisions 和最终结果均绑定指纹。
- **统计口径可复算**：频率按独立 JD 数计算，同一 JD 的重复实例只贡献一次覆盖；报告
  逐项回链原始 requirement 与 evidence。

## 环境与公开 Sample

需要 Python 3.11 和 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync
uv run python -m scripts.make_sample_report
```

sample 使用虚构公司、岗位和要求，在临时 SQLite 数据库中调用正式统计与报告渲染代码。
它不需要 `.env`、不会调用 LLM、不会读取私有数据。

验证重新生成结果一致：

```powershell
uv run python -m scripts.make_sample_report
git diff --exit-code -- examples/market-report-sample.md
```

## 正式链主要命令

使用真实数据前复制 `.env.example` 为 `.env` 并填写 LLM 配置。付费调用必须显式
`--execute`；数据库操作必须显式选择目标。

```powershell
# 1. 导入 JD
uv run python -m app.cli import-jds data/raw_jds --use-project-database

# 2. 抽取 acceptance（付费；完整范围、多次运行）
uv run python -m scripts.experiments.p0_3.run_real_jd_acceptance `
  --use-project-database --all --execute

# 3. 对 acceptance 生成的单份 JD report/raw 完成人工审核后定稿
uv run python -m app.cli finalize-extraction `
  --report data/private/extraction-report.json `
  --raw-output data/private/extraction-raw.json `
  --job-id 1 --use-project-database

# 4. 归并 acceptance（付费；不传 --job-ids 即全部 JD）
uv run python -m scripts.experiments.p0_4.run_acceptance `
  --database-url "sqlite:///data/jd_skill_insight.db" `
  --raw-output data/private/consolidation-acceptance-raw.json `
  --execute

# 5. 稳定性分析与人工裁决后定稿
uv run python -m app.cli finalize-consolidation `
  --report reports/P0-4/acceptance-report.json `
  --raw-output data/private/consolidation-acceptance-raw.json `
  --final-result data/private/consolidation-final.json `
  --review-decisions data/private/review-decisions.json `
  --use-project-database

# 6. 只读审计、验证和报告
$consolidationId = Read-Host "Consolidation ID"
uv run python -m app.cli audit-extraction-sources --use-project-database
uv run python -m app.cli audit-consolidation `
  --consolidation-id $consolidationId --use-project-database
uv run python -m app.cli validate-consolidation `
  --consolidation-id $consolidationId --use-project-database
uv run python -m app.cli generate-report `
  --consolidation-id $consolidationId --use-project-database
```

归并验收脚本只接受 `--database-url`；实验时优先使用正式数据库的临时副本。完整脚本
导航和参数边界见 [scripts/README.md](scripts/README.md)。

可选单次预检命令：

```powershell
uv run python -m app.cli extract-jds --all `
  --candidate-output data/private/extraction-candidate.json `
  --use-project-database --execute

uv run python -m app.cli consolidate-requirements --all `
  --candidate-output data/private/consolidation-candidate.json `
  --use-project-database --execute
```

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
- LLM 抽取与归并存在随机性，正式结果仍需要人工审核与裁决；
- 若既有正式抽取缺少现行机器可验证 provenance，报告只在私有、范围受限的结构化
  waiver 明确覆盖时放行，并必须披露风险；新增数据不得继承例外；
- 匿名工程验收摘要可以公开，但市场结论和私有批次明细不能从公开 clone 逐字复算；
- 项目不提供 Web UI、在线服务、简历匹配、ATS 或自动投递 Agent。

## 文档导航

| 文档 | 职责 |
|---|---|
| [CURRENT_STATE](docs/CURRENT_STATE.md) | 当前软件基线、安全门和公开/私有边界 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 正式链、可选预检、模块边界和设计理由 |
| [GLOSSARY](docs/GLOSSARY.md) | 核心业务术语与流水线不变量 |
| [EXTRACTION_RULES](docs/EXTRACTION_RULES.md) | 职责边界、岗位要求、逻辑组和字段规则 |
| [VALIDATION](docs/VALIDATION.md) | 证据、覆盖、抽取与归并验收合同 |
