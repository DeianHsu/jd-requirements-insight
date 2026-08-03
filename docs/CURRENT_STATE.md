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

## 当前数据规模（本地私有，不入库提交）

- `data/raw_jds/`：5 份真实 JD 原文（私有，Git 忽略）。
- `data/jd_skill_insight.db`：**旧版本派生数据库**——包含已删除的
  `requirement_relations`（458 行）、`job_responsibilities`（210 行）表，
  抽取均为旧版本（prompt:1.0~2.3.1、schema:1.0/2.0），归并批次 5 个均为
  旧版本产物。当前代码只支持 v0.8 + Schema V3，不做兼容或迁移。

  **需要用户手动处理**：备份 `data/raw_jds/` 后删除 `data/jd_skill_insight.db`，
  用 v0.8 重新导入与抽取（代码不会自动删除用户本地数据库）。

## 尚未执行的真实验证

- v0.8 + Schema V3 抽取：未在真实 JD 上运行（P0-3B 未执行）；
- 规则场景验收：P0-3A 未执行（需授权付费调用）；
- P0-4 归并验收：未在 v0.8 抽取结果上执行（旧库无 v0.8 数据）；
- 市场统计：模块已就绪并有端到端测试，未消费真实归并批次。

## 当前已知问题

- 本地数据库为旧结构，需要用户备份后重建；
- 无真实 JD 的 v0.8 抽取结果，统计与报告暂无真实输入；
- P0-4 归并合同验证缺省自动选择唯一共同当前抽取版本；显式旧版本会被拒绝（符合预期）。

## 下一步开发任务

1. （用户操作）备份 `data/raw_jds/`，删除旧数据库；
2. （授权付费）P0-3B 小规模真实 JD 验证（1～3 份）；
3. （授权付费）P0-4 归并验收（v0.8 抽取结果）；
4. 实现 `app.cli generate-report --consolidation-id <ID> --output report.md`：
   消费 `app/market_analysis.py` 输出 Markdown 市场分析报告（含证据追溯）。

## 付费与私有数据依赖

- 付费：抽取（v0.8 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
