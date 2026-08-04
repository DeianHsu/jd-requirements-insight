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

## 当前数据状态（本地私有，不入库提交）

- 数据库已使用现行 Schema 创建（六张业务表，无旧表）；
- 5 份真实 JD 已导入 `data/jd_skill_insight.db`（重复导入幂等跳过）；
- **当前无 v0.8 抽取结果（`job_extractions` 与 `job_requirements` 均为空），
  无归并批次**；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## P0-3A 验证结果

### 原始真实运行（2026-08-04，已授权付费，一次运行）

- 环境：deepseek-v4-flash、prompt 0.8、schema 3.0、max_attempts=2、
  13 场景 × base+transformed 各 1 次；
- **hard_gate_failures=7，warnings=1，未通过**；
- 报告：`reports/P0-3/acceptance-20260804-000314-report.json`（脱敏）；
- 原始响应：`data/private/experiments/p0_3/acceptance-20260804-000314-raw.json`
  （私有，仅本地分析）。

### 离线重算（同一批响应，未调用模型，验证器修复+收缩后）

报告：`reports/P0-3/acceptance-20260804-000314-revalidated.json`

- 原 hard gate：7 → **新 hard gate：5**；原 warning：1 → 新 warning：0；
- 已消除的假阳性：SCN-003/SCN-010 no_new_conditions（evidence 列表
  序号前缀）、SCN-006 fact_set/importance_expected_change（配对与
  锚点解析）、SCN-007 group_members_preserved ×2（any_of 检查与块
  粒度）、SCN-009 no_new_conditions（拆句）、group_change_anchor
  warning；
- 剩余 5 个失败全部为真实模型或场景问题（见下）。

## 已确认的验证器问题（已修复并收缩）

- evidence 开头列表标记归一（`1. `/`1、`/`(1) `/`一、`/`- `/`• `）与
  正文语义数字保留（`3年` vs `5年`、`Python 3` 不被误归并）；
- 配对依据收敛为可靠语义：归一后 evidence 相同（主分组）、或 raw_name
  明确包含/相等且无歧义（fallback）；字段（category/importance/
  proficiency/group_logic）只用于候选排序，不能单独证明同一事实；
  字段相同但名称无关的项保持 unmatched；
- `group_change_anchor` 为元数据键（不再产生未知属性 warning）；
- 块拆分/合并（多对一块对齐）与 any_of+standalone 混合检查正确。

## 剩余模型或场景问题（未修复，待决策）

- **SCN-006（真实 GROUP 错误）**：模型把“有技术甲和框架乙相关项目
  经验者优先”（“和”关系）拆为两项并建 any_of 组；group_type 与
  group_membership 检查失败。建议独立 Prompt 版本升级任务（新增
  GROUP 规则正反例：仅“至少一种/任一/或”建 any_of，“和”保持
  standalone）。
- **SCN-006（场景期望与规则矛盾）**：场景期望 proficiency 不变，但
  “相关项目经验”按 Schema V3 规则应判 unknown（SCN-013 同款表达）。
  模型行为符合规则，需要修正场景期望。
- **SCN-007/SCN-008（占位词 category 漂移）**：框架乙（other ↔
  software_engineering）、能力丙使用经验（other ↔ experience）、
  技术丙/框架丁 改名后 category 判定不稳定。占位词类别语义不明，
  建议场景词替换为类别明确的中性描述（不删除 category invariance
  检查）。

## 下一步

1. 决定是否提交独立 Prompt 版本升级任务（SCN-006 GROUP 规则）与
   场景期望修正（SCN-006 proficiency、SCN-007/008 占位词）；
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
