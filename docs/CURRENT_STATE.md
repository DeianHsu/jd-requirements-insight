# 当前状态

updated_at: 2026-08-04

## 当前可运行功能

| 命令 | 功能 |
|---|---|
| `python -m app.cli import-jds <目录>` | 导入 Markdown JD（frontmatter + 正文） |
| `python -m app.cli list-jds` | 列出 JD 摘要 |
| `python -m app.cli extract-jds [--all|--job-id N] --execute` | v0.10 + Schema V3 两段式抽取（付费，需 .env 配置与 --execute） |
| `python -m app.cli list-extractions` | 列出抽取结果 |
| `python -m app.cli consolidate-requirements --all|--job-id N --execute` | 跨 JD 归并为 canonical requirement（付费，需 --execute） |
| `python -m app.cli list-consolidations` | 列出归并批次 |
| `python -m app.cli validate-consolidation --consolidation-id N` | 离线验证（不付费；回查批次真实输入，失败返回非零） |
| `app/market_analysis.py` | 市场统计（实例数、独立 JD 数、importance 分布、来源证据、稳定排序），供下一阶段 `generate-report` 消费 |

验证脚本（均需 `--execute` 才调用付费模型，`--dry-run` 预检不付费）：

- P0-3A 规则场景：`python -m scripts.experiments.p0_3.run_acceptance --execute`
- P0-3B 真实 JD：`python -m scripts.experiments.p0_3.run_real_jd_acceptance --use-project-database --all --execute`
- P0-4 归并验收：`python -m scripts.experiments.p0_4.run_acceptance --execute`
  （缺省自动选择所选 JD 的唯一共同 v0.10 + Schema V3 抽取版本；查询前
  验证数据库结构；顺序变形合同违规计入 hard gate）
- P0-4 小规模预检：`python -m scripts.experiments.p0_4.run_small_scale_precheck --execute`

## 当前数据状态（本地私有，不入库提交）

- 数据库已使用现行 Schema 创建（六张业务表，无旧表）；
- 5 份真实 JD 已导入 `data/jd_skill_insight.db`（重复导入幂等跳过）；
- **已持久化正式抽取结果：JD 1/2/3**（deepseek-v4-flash、
  `prompt:0.10|schema:3.0`，要求数 37/30/16，幂等已验证）；
- **无归并批次**（P0-4 未执行）；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## P0-3A 规则场景验收（2026-08-04，已授权付费）

- 环境：deepseek-v4-flash、**Prompt 0.10**、schema 3.0、max_attempts=2、
  13 场景 × base+transformed 各 1 次；
- **hard_gate_failures = 0，warnings = 0**，通过；
- 报告：`reports/P0-3/acceptance-20260804-172141-report.json`（脱敏）；
- 原始响应：`data/private/experiments/p0_3/acceptance-20260804-172141-raw.json`
  （私有，仅本地分析）。

## P0-3B 真实 JD 验收（2026-08-04，已授权付费）

- JD 1（3 次独立抽取，37/39/37 条）：
  `reports/P0-3/real-jd-acceptance-20260804-174748-report.json`
- JD 2、3（各 3 次，30/30/28、18/14/14 条）：
  `reports/P0-3/real-jd-acceptance-20260804-175840-report.json`
- 三份 JD 累计：**所有运行完整、hard gate = 0**；
- 人工语义审计：无 evidence 幻觉、无职责泄漏、无隐含技能补出；
  importance/proficiency 三次运行稳定；any_of 组（"或"替代关系）
  判定正确；
- 非阻塞 warning（已分类，不影响 canonical 归并）：
  1. raw_name 表述漂移（同证据不同命名，如"需求理解"vs"需求理解
     能力"）→ 归并阶段归一，不影响统计结论；
  2. 拆分粒度漂移（±2~4 条 instance，如"乐于沉淀规范、模板和可复用
     能力"1 vs 3 条）→ 影响 instance_count 噪声，canonical 集合不变；
  3. any_of 组成员漂移（任务调度/流程引擎/规则引擎等）→ 影响 group
     统计，P0-4 重点观察；
  4. 边缘 category 漂移（Workflow agent_capability/agent_framework、
     算法功底 software_engineering/other 等）→ 轻微影响 category 分布。

## 是否达到进入 P0-4 的条件

**是。** Prompt 0.10 + Schema V3 的抽取结果已通过 P0-3A（13 场景
hard gate=0）与 P0-3B（JD 1/2/3 累计 hard gate=0、人工审计无阻塞
问题），正式抽取数据已持久化且幂等。剩余非阻塞漂移作为 P0-4 观察点。

## 下一步

1. 执行 P0-4 小规模预检（按真实 requirement instance 数量选择
   target-size，不超过实际可用数量）；
2. 执行 P0-4 正式验收（coverage=100%、结构违规=0、顺序变形无合同
   失败、人工检查所有多成员 cluster）；
3. 生成并离线验证正式归并批次；
4. 实现 `generate-report`（市场统计 + 证据追溯 Markdown 报告）。

## 付费与私有数据依赖

- 付费：抽取（v0.10 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
