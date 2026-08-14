# JD Requirements Insight

把非结构化招聘 JD 转化为**可统计、可审核、可追溯到原文证据的岗位要求市场报告**。
项目关注的不是“让模型总结几份 JD”，而是如何把随机的 LLM 输出收口成具有明确身份、
唯一映射、人工审核记录和可复现统计口径的正式数据产品。

[查看合成公开报告](examples/market-report-sample.md) ·
[查看架构说明](docs/ARCHITECTURE.md) ·
[查看验证协议](docs/annotation/VALIDATION.md)

## MVP 结果

当前 v0.1 MVP 已完成 15 份真实 JD 的端到端闭环；真实 JD、公司信息、模型原始响应和
最终真实报告均保留在私有目录，不进入公开仓库。

| 指标 | 已关闭批次结果 |
|---|---:|
| JD | 15 |
| Requirement instances | 409 |
| Canonical requirements | 329 |
| 跨 JD 共同要求 | 43 |
| 单 JD 长尾要求 | 286 |
| 覆盖最高的要求 | 团队协作能力（9/15 JD） |
| 正式归并质量门禁 | Coverage 100%，结构违规 0，reportable=True |

以上数字来自已审核并正式定稿的 consolidation #5。它们只描述当前小样本，**不代表行业
排名或完整市场结论**。公开仓库提供使用同一统计与渲染代码生成的合成报告，便于在不泄露
真实招聘数据的前提下检查结果形态和复现方式。

## 完整 Pipeline

```text
Markdown JD 导入与去重
→ v0.10 + Schema V3 两段式结构化抽取
→ 规则场景 / 真实 JD 多次运行验收
→ 人工审核后 finalize-extraction
→ requirement instance 跨 JD 归并候选
→ 多次运行 + 顺序变形 + 稳定性分析
→ 人工 must-link / cannot-link 裁决
→ finalize-consolidation 原子定稿
→ 独立 JD 口径统计 + 原文 evidence 追溯
→ 确定性 Markdown 市场报告
```

模型只负责提出抽取和归并候选；正式表只接收通过合同检查、身份校验和人工审核的结果。

## 核心工程亮点

- **事实层与标准层分离**：每条原子要求作为 requirement instance 保留原文 evidence；
  canonical requirement 只负责跨 JD 标准化，二者通过唯一 mapping 连接。
- **候选与正式数据隔离**：模型输出先落私有候选/验收产物，不能直接写正式业务表；
  finalize 入口校验输入、运行、审核决定和结果 fingerprint 后原子写入，并支持幂等复跑。
- **评测不是单一准确率**：抽取层包含证据逐字校验、规则场景变形和真实 JD 多次运行；
  归并层检查 exact coverage、结构违规、positive-pair Jaccard、顺序变形和稳定性漂移。
- **Human-in-the-loop 可追溯**：人工裁决以 must-link / cannot-link 和名称 override
  记录，批准 run、review-decisions 与 final result 均有独立 fingerprint。
- **统计口径可复算**：市场频率按独立 JD 数计算，同一 JD 的重复实例只贡献一次覆盖；
  报告只读取已完成定稿且 provenance 合法的归并批次，并逐项回链原文证据。

## 运行公开 Sample

```powershell
uv sync
uv run python -m scripts.make_sample_report
```

该命令使用脚本内的虚构公司、岗位和要求，在临时 SQLite 数据库中走正式统计与报告渲染
代码，并确定性生成 [examples/market-report-sample.md](examples/market-report-sample.md)。
它不需要 `.env`、不会调用 LLM，也不会读取 `data/private/`。

验证仓库中的 sample 与重新生成结果一致：

```powershell
uv run python -m scripts.make_sample_report
git diff --exit-code -- examples/market-report-sample.md
```

## 主要入口

使用真实数据前，复制 `.env.example` 为 `.env` 并填写 LLM 配置。抽取和归并候选会产生
付费请求，必须显式提供 `--execute`；所有数据库操作必须显式选择目标。

```powershell
# 导入 JD（Markdown 目录）
uv run python -m app.cli import-jds data/raw_jds --use-project-database

# 生成抽取候选（付费；不写正式抽取表）
uv run python -m app.cli extract-jds --all `
  --candidate-output data/private/extraction-candidate.json `
  --use-project-database --execute

# 人工批准完整验收产物后，离线定稿正式抽取
uv run python -m app.cli finalize-extraction `
  --report data/private/extraction-report.json `
  --raw-output data/private/extraction-raw.json `
  --job-id 1 --use-project-database

# 生成归并候选（付费；不写正式归并表）
uv run python -m app.cli consolidate-requirements --all `
  --candidate-output data/private/consolidation-candidate.json `
  --use-project-database --execute

# 多次运行、稳定性分析和人工裁决后，离线定稿正式归并
uv run python -m app.cli finalize-consolidation `
  --report data/private/consolidation-report.json `
  --raw-output data/private/consolidation-raw.json `
  --final-result data/private/consolidation-final.json `
  --review-decisions data/private/review-decisions.json `
  --use-project-database

# 离线审计、验证和报告；<id> 替换为当前数据库中的正式批次 ID
uv run python -m app.cli audit-extraction-sources --use-project-database
uv run python -m app.cli audit-consolidation --consolidation-id <id> --use-project-database
uv run python -m app.cli validate-consolidation --consolidation-id <id> --use-project-database
uv run python -m app.cli generate-report --consolidation-id <id> --use-project-database
```

完整验收脚本位于 `scripts/experiments/`。CLI 职责见 [app/README.md](app/README.md)，
实验编排边界见 [scripts/README.md](scripts/README.md)。

## 评测与可复现性

```powershell
uv run python -m app.cli --help
uv run pytest
uv run ruff check app scripts tests
```

- P0-3A：领域中性规则场景 + 确定性变换，检查 evidence、职责边界和 Schema 合同。
- P0-3B：真实 JD 多次独立抽取、漂移索引、人工审核和来源 fingerprint 绑定。
- P0-4：三次独立归并 + 顺序变形、exact coverage、结构违规、Jaccard、稳定性分析和
  人工裁决；扩样时用 frozen baseline 保证已关闭的旧分区不会被新模型运行改写。
- 回归：正式 CLI E2E 覆盖候选隔离、人工定稿、审计、报告门禁、幂等和失败回滚；
  测试不调用付费外部服务。

公开 clone 可以复现合成 sample 和自动化测试；15 JD 真实批次因隐私不公开，因此不能在
公开环境逐字复算真实报告，但其规模、门禁结果和已知 provenance 限制会保留在文档中。

## 数据和隐私边界

- 真实 JD（`data/raw_jds/`）、数据库、密钥与原始模型响应（`data/private/`）
  属于私有材料，不提交 Git；
- 验证/验收报告只输出统计与脱敏索引，原始运行结果只写私有目录；
- 付费 LLM 调用必须显式 `--execute` 确认；
- 所有数据库命令必须显式选择 `--database-url` 或
  `--use-project-database`；候选模型结果不得直接写正式业务表；
- 当前只支持 v0.10 + Schema V3：旧抽取数据与旧数据库结构不做兼容或迁移，
  遇到时备份原始 JD、删除旧派生数据库并重新生成。

## 仓库结构

| 路径 | 作用 |
|---|---|
| `app/` | 导入、抽取、归并、finalize、审计、统计和报告的正式实现 |
| `scripts/experiments/` | 多次运行验收、变形分析、稳定性分析与人工裁决应用 |
| `tests/` | 合同、失败门禁、幂等、provenance 和正式 CLI E2E |
| `docs/annotation/` | Requirement、responsibility、evidence 与验证规则 |
| `examples/` | 不含真实公司或 JD 的公开合成报告 |
| `data/private/` / `data/raw_jds/` | 本地私有材料，Git 忽略 |

## 已知限制

- 当前真实样本只有 15 份 JD，结果适合展示工程闭环，不足以代表完整招聘市场。
- LLM 抽取与语义归并存在随机性；稳定性指标是诊断信号，最终结果仍需要人工裁决。
- 真实 MVP 中 JD1～3 是早期历史抽取，保持 `unverified`，仅通过范围受限的 P0-7
  historical waiver 供当前 MVP 报告消费；JD4～15 为 `fully_bound`。报告不会隐藏该风险。
- 项目是本地 CLI 数据分析流水线，不提供 Web UI、在线服务、简历匹配或 ATS 功能。
- 公开 sample 证明统计与报告代码可复现，不包含真实 15 JD，也不模拟付费模型质量。

## 文档入口

| 文档 | 内容 |
|---|---|
| [PROJECT_PLAN](docs/PROJECT_PLAN.md) | P0-1～P0-8 阶段状态与 MVP 关闭结果 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 数据流、模块边界与架构理由 |
| [CURRENT_STATE](docs/CURRENT_STATE.md) | 当前功能、正式数据规模与已知限制 |
| [GLOSSARY](docs/GLOSSARY.md) | 核心业务术语 |
| [REQUIREMENTS](docs/annotation/REQUIREMENTS.md) | 岗位要求与逻辑组规则 |
| [RESPONSIBILITIES](docs/annotation/RESPONSIBILITIES.md) | 职责边界规则 |
| [VALIDATION](docs/annotation/VALIDATION.md) | 证据、规则场景与验证协议 |
