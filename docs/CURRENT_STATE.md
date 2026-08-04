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

## P0-3A 真实验收结果（2026-08-04，已授权付费，一次运行）

**未通过**：hard_gate_failures=7，warnings=1，exit 1。

- 报告：`reports/P0-3/acceptance-20260804-000314-report.json`（脱敏）
- 原始响应：`data/private/experiments/p0_3/acceptance-20260804-000314-raw.json`
  （私有，仅本地分析）
- 环境：deepseek-v4-flash、prompt 0.8、schema 3.0、max_attempts=2、
  runs=1（每场景 base + transformed 各一次）

失败归因（基于脱敏报告与私有原始响应本地分析）：

- **主因（假阳性，验证器锚点匹配缺陷）**：base 与 transformed 的模型
  证据截取起点不稳定（同一句有时带序号前缀 `1. `、有时不带）；
  `_pair_items` 按 `_alnum(evidence)` 分组配对时数字被保留，导致
  base↔variant 相同条件无法配对，连锁产生 SCN-003/006/007/008/
  009/010 的 no_new_conditions、fact_set_preserved、field_invariance
  （category 50%/75%）、group_members_preserved 假失败（SCN-003 的
  base 与 variant 抽取内容完全一致仍报 4 个 new_conditions）。
- **真实模型问题（次要，配对修复后仍会暴露）**：SCN-006 把
  “有技术甲和框架乙相关项目经验者优先”（“和”关系）拆为两项并建
  any_of 组；SCN-007 框架乙 category 漂移 other →
  software_engineering；证据截取范围不稳定本身（EVID-01 最短原则
  执行不一致，属稳定性级）。
- **验证器小缺陷（warning 级）**：未识别场景期望属性
  `group_change_anchor`。

## 尚未执行的真实验证

- P0-3A 已执行但未通过（见上），重跑待定点修正后授权；
- 真实 JD 的 v0.8 + Schema V3 抽取未执行（P0-3B 未执行）；
- P0-4 归并验收未执行（无 v0.8 抽取结果）；
- `generate-report` 未实现（P0-5 剩余项）。

## 当前已知问题

- P0-3A 验证器锚点匹配对 evidence 序号前缀敏感（假阳性主因），
  需要定点修正后重跑 P0-3A；
- 模型存在证据截取起点不稳定（序号前缀）与少量真实语义漂移
  （“和”建 any_of、category 漂移），需在三份验证中重点观察。

## 下一步开发任务

1. 定点修正 P0-3A 暴露的问题（验证器锚点匹配 + 真实模型问题评估）；
2. 重新执行 P0-3A 规则场景真实验收；
3. 通过后对单份真实 JD（ID 1）执行 P0-3B；
4. 单份验证通过后扩大到三份 JD（ID 1/2/3）；
5. 三份验证通过后持久化 v0.8 + Schema V3 抽取结果；
6. 执行 P0-4 预检、正式验收和正式归并；
7. 验收通过后实现 `generate-report`。

## 付费与私有数据依赖

- 付费：抽取（v0.8 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
