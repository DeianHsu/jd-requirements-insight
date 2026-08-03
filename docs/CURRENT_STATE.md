# 当前状态

updated_at: 2026-08-04
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

最近一次真实数据准备（2026-08-04，无付费调用）后：

- 当前数据库已使用现行 Schema 创建（六张业务表，无旧表）；
- 5 份真实 JD 已重新导入 `data/jd_skill_insight.db`（重复导入幂等跳过）；
- 当前尚无 v0.8 抽取结果；
- 当前没有归并批次；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## 已完成的真实验证准备（均无付费调用）

- P0-3A dry-run 通过：13 个规则场景锚点计划输出，返回成功；
- P0-3B dry-run 通过：单份（ID 1）与三份（ID 1/2/3）均输出输入
  fingerprint 与运行计划，返回成功；
- `extract-jds` 计划模式正确：显示模型名（deepseek-v4-flash）、
  v0.8 + Schema V3、选中 JD 数与 --execute 提示，不初始化客户端、
  不产生抽取记录；
- 全量自动化测试与 Ruff 通过。

## 尚未执行的真实验证

- 规则场景验收 P0-3A 未执行（需授权付费调用）；
- 真实 JD 的 v0.8 + Schema V3 抽取未执行（P0-3B 未执行）；
- P0-4 归并验收未执行（无 v0.8 抽取结果）；
- `generate-report` 未实现（P0-5 剩余项）。

## 当前已知问题

- 暂无阻塞性已知问题；下一步为等待授权执行付费验证。

## 下一步开发任务（等待授权）

1. 授权后执行 P0-3A 规则场景验收（`--execute`）；
2. 单份 JD（ID 1）P0-3B 真实抽取验证（`--execute`）；
3. 三份 JD（ID 1/2/3）P0-3B 验证；
4. P0-3B 通过后持久化抽取结果（`extract-jds --execute`）；
5. 根据持久化抽取的真实 requirement instance 数量选择 target-size，
   执行 P0-4 预检与正式验收；
6. 验收通过后实现 `generate-report`。

## 付费与私有数据依赖

- 付费：抽取（v0.8 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
