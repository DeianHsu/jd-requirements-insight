# 当前状态

updated_at: 2026-08-03
implementation_baseline: 5c3255e（仓库收缩完成；Git 是当前 HEAD 的唯一事实来源）

## 当前可运行功能

| 命令 | 功能 |
|---|---|
| `python -m app.cli import-jds <目录>` | 导入 Markdown JD（frontmatter + 正文） |
| `python -m app.cli list-jds` | 列出 JD 摘要 |
| `python -m app.cli extract-jds [--all|--job-id N] --execute` | v0.8 + Schema V3 两段式抽取（付费，需 .env 配置与 --execute） |
| `python -m app.cli list-extractions` | 列出抽取结果 |
| `python -m app.cli consolidate-requirements --all|--job-id N --execute` | 跨 JD 归并为 canonical requirement（付费，需 --execute） |
| `python -m app.cli list-consolidations` | 列出归并批次 |
| `python -m app.cli validate-consolidation --consolidation-id N` | 离线验证（不付费；回查批次真实输入，失败返回非零） |
| `app/market_analysis.py` | 市场统计（实例数、独立 JD 数、importance 分布、来源证据、稳定排序），供下一阶段 `generate-report` 消费 |

验证脚本（均需 `--execute` 才调用付费模型，`--dry-run` 预检不付费）：

- P0-3A 规则场景：`python -m scripts.experiments.p0_3.run_acceptance --execute`
- P0-3B 真实 JD：`python -m scripts.experiments.p0_3.run_real_jd_acceptance --use-project-database --all --execute`
- P0-4 归并验收：`python -m scripts.experiments.p0_4.run_acceptance --execute`
  （缺省自动选择所选 JD 的唯一共同 v0.8 + Schema V3 抽取版本；查询前
  验证数据库结构；顺序变形合同违规计入 hard gate）
- P0-4 小规模预检：`python -m scripts.experiments.p0_4.run_small_scale_precheck --execute`

## 当前数据规模（当前维护环境，本地私有，不入库提交）

最近一次本地工作区整理后：

- `data/raw_jds/`：5 份真实 JD 原文仍保留，属于私有输入（Git 忽略；
  重新克隆仓库的环境不会包含这些私有文件）。
- `data/jd_skill_insight.db`：当前不存在；旧派生数据库已在本地工作区
  整理时删除。
- 当前没有 v0.8 + Schema V3 抽取结果，也没有当前归并批次。

## 尚未执行的真实验证

- 尚未使用当前 Schema 重新创建数据库；
- 尚未运行真实 JD 的 v0.8 + Schema V3 抽取（P0-3B 未执行）；
- 规则场景验收 P0-3A 未执行（需授权付费调用）；
- P0-4 归并验收未执行（无 v0.8 抽取结果）；
- 市场统计模块已有测试，但暂无真实归并批次可消费。

## 当前已知问题

- 尚未使用当前 Schema 重新创建数据库；
- 尚未运行真实 JD 的 v0.8 + Schema V3 抽取；
- 尚未执行 P0-3B 与 P0-4 真实验证；
- 市场统计模块已有测试，但暂无真实归并批次可消费。

## 下一步开发任务

1. 使用当前代码重新导入 `data/raw_jds/`；
2. 对 1～3 份 JD 执行 v0.8 + Schema V3 抽取（授权付费）；
3. 执行 P0-3B 人工检查；
4. 执行 P0-4 小规模预检与正式验收；
5. 验收通过后实现 `generate-report`。

## 付费与私有数据依赖

- 付费：抽取（v0.8 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
