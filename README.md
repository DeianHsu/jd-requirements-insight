# JD Skill Insight

把真实 JD 转化为**可统计、可追溯的市场要求报告**：导入 → v0.8 + Schema V3
结构化抽取 → 抽取质量验证 → requirement instance 归并为 canonical
requirement → 独立 JD 统计 → 原文证据追溯 → Markdown 市场分析报告。

## 当前具备的能力

- **JD 导入**：Markdown JD（frontmatter + 正文）批量导入与内容去重；
- **v0.8 结构化抽取**：两段式（发现段全局分句归属 + 判断段局部语义判断）、
  三级熟练度、any_of 逻辑组、原文证据强制校验、有限重试、幂等持久化；
- **抽取质量验证**：P0-3A 规则场景变形测试（领域中性场景 + 确定性变换）、
  P0-3B 真实 JD 验证（合同检查、漂移、异常项索引）；
- **要求事实归并**：单次 LLM 聚类输出 canonical requirement 与来源
  实例分区（不确定时创建 singleton）；mappings 由确定性代码生成并
  幂等持久化；
- **归并验证**：coverage、结构违规、positive-pair Jaccard、canonical/
  singleton 漂移、顺序/分块变形、人工 cluster 复核；
- **市场统计**：每个 canonical requirement 的实例数、独立 JD 数、
  must/preferred/mentioned 分布、来源 JD 集合与原文证据（`app/market_analysis.py`）。

## 安装

```powershell
uv sync
```

复制 `.env.example` 为 `.env` 并填写 LLM 配置（抽取与归并调用付费模型，
未配置时相关命令会提示缺少字段）。

环境验证：

```powershell
uv run python -m app.cli --help
uv run pytest
uv run ruff check app scripts tests
```

## 当前主线命令

```powershell
# 导入 JD（Markdown 目录）
python -m app.cli import-jds data/raw_jds

# 抽取（付费调用必须 --execute 确认）
python -m app.cli extract-jds --all --execute

# 归并（付费调用必须 --execute 确认）
python -m app.cli consolidate-requirements --all --execute

# 查看与验证
python -m app.cli list-jds
python -m app.cli list-extractions
python -m app.cli list-consolidations
python -m app.cli validate-consolidation --consolidation-id 1
```

验证脚本（付费调用必须显式 `--execute`；`--dry-run` 预检不付费）：

```powershell
# P0-3A 规则场景验证
python -m scripts.experiments.p0_3.run_acceptance --dry-run
python -m scripts.experiments.p0_3.run_acceptance --execute

# P0-3B 真实 JD 验证（默认项目数据库）
python -m scripts.experiments.p0_3.run_real_jd_acceptance --use-project-database --all --execute

# P0-3B 临时数据库示例
python -m scripts.experiments.p0_3.run_real_jd_acceptance --database-url sqlite:///data/private/p0_3_validation.db --all --execute

# P0-4 归并验收 / 小规模预检
python -m scripts.experiments.p0_4.run_acceptance --execute
python -m scripts.experiments.p0_4.run_small_scale_precheck --execute
```

## 数据和隐私边界

- 真实 JD（`data/raw_jds/`）、数据库、密钥与原始模型响应（`data/private/`）
  属于私有材料，不提交 Git；
- 验证/验收报告只输出统计与脱敏索引，原始运行结果只写私有目录；
- 付费 LLM 调用必须显式 `--execute` 确认；
- 当前只支持 v0.8 + Schema V3：旧抽取数据与旧数据库结构不做兼容或迁移，
  遇到时备份原始 JD、删除旧派生数据库并重新生成。

## 文档入口

| 文档 | 内容 |
|---|---|
| `docs/PROJECT_PLAN.md` | 项目目标、六个阶段状态、下一步、MVP 条件 |
| `docs/ARCHITECTURE.md` | 当前架构与架构理由 |
| `docs/CURRENT_STATE.md` | 当前 HEAD、可运行功能、数据规模、已知问题 |
| `docs/GLOSSARY.md` | 核心业务术语 |
| `docs/annotation/REQUIREMENTS.md` | 岗位要求规则（REQ/GROUP/FIELD） |
| `docs/annotation/RESPONSIBILITIES.md` | 职责边界规则（RESP） |
| `docs/annotation/VALIDATION.md` | 证据、规则场景与验证协议 |
