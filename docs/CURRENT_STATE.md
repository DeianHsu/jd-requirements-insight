# 当前状态

updated_at: 2026-08-05

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
- **已持久化正式归并批次：1 份**（job_ids=1,2,3，83 条精确覆盖，
  deepseek-v4-flash `prompt:4.3|schema:3.0`，来源 run-1 + 人工审核决定，
  审核指纹 `51cebada…`、结果指纹 `c5be704e…`）；旧候选批次已按
  "不维护旧派生结果"原则删除重建，身份见 `reports/P0-4/previous-batch-note.json`；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## P0-4 要求归并定稿（2026-08-05，离线完成）

- **Prompt 基线冻结为 4.3**：比较 4.2（Jaccard 64/50/73/78）与 4.3
  （60/70/58/43）；4.3 明确修复两个重要错误（56/74 AI/LLM 落地经验
  漏并、71/72 LangChain/AutoGen 错误合并），因此保留 4.3，不再继续
  调整 Prompt；
- **验收**（3 次独立运行 + 顺序变形）：coverage=100%、结构违规=0；
- **业务影响稳定性分析**（`analyze_stability.py`，4 个观察 = 3 独立
  + 成功顺序变形）：基于每个观察的完整 canonical 分区（含与核心
  成员同簇的全部实例），稳定对 5 个（4/4 同簇）、不稳定对 9 个；
  **市场影响 canonical 1 个**：团队协作族（23/81 核心）完整成员在
  各观察为 [23,81]→2 JD、[23,53,81]→3、[23,27,81]→2、[23,27,53,81]→3，
  distinct job count 2↔3 漂移（旧口径只统计核心成员会漏报）；
  公共报告 `reports/P0-4/stability-report.json`（脱敏）、私有分析
  `data/private/experiments/P0-4/stability-analysis.json`（含名称/
  evidence）；
- **人工裁决**：`data/private/experiments/P0-4/review-decisions.json`
  （私有，与输入指纹绑定）——must-link 5 组（5-46、11-43、
  23-27-53-81、17-45、**56-74**），cannot-link 1 组（71-72）；
  覆盖全部 8 个 unstable 跨 JD 对（56-74 为新口径下确认的漏裁决：
  4 观察中 3 次合并、仅顺序变形漏并，属模型漏归并）；团队协作
  canonical 的 JD 覆盖数由裁决确定为 3，不再依赖随机运行；
- **确定性应用**：`apply_review_decisions.py` 从验收运行 run-1 生成
  最终结果（canonical=72、mappings=83、coverage=1.0、结构违规=0），
  记录来源运行指纹与审核决定文件指纹；cannot-link 拆分使用对应
  requirement 的原始名称，不生成"（拆分）实例N"占位名；
- **定稿安全门**：`finalize_consolidation.py` 核对报告↔raw 全部身份
  （input_fingerprint/extractor/model/prompt/schema/selected_job_ids/
  run_count）、审核绑定（approved_run_index + approved_result_fingerprint
  + reviewed_at 格式）、精确 ID 覆盖（数量相同但 ID 被替换也拒绝）、
  占位名称检测、**幂等安全门**（已有批次只有在最终结果指纹、审核
  决定指纹、来源运行标识全部一致时才允许复用，否则明确拒绝且不修改
  已有批次；缺审核元数据的旧格式批次拒绝无依据宣称一致）；
- **离线归并验证**：持久化批次精确 ID 一致性检查通过（83 条全覆盖、
  无缺失/多余/重复归属/归属冲突）；重复定稿幂等（仅 1 份批次）；
- **大模块 2（要求归并质量闭环）已关闭**：稳定性分析使用完整
  canonical 成员、顺序变形纳入业务分析、稳定性判定按实际观察总数；
  相同身份但不同最终结果会被明确拒绝、真正相同的最终结果保持幂等；
  cannot-link 不产生占位名称；审核身份与结果链条完整可追溯；尚未
  进入报告生成模块；
- **范围声明**：当前只证明 3 份 JD（83 条实例）范围的归并质量；
  15～20 份 JD 扩展仍需分阶段验证（新增 JD 会引入新的边界对，需
  重新走稳定性分析 + 人工裁决流程）。

## P0-5 市场报告闭环（2026-08-05，离线完成）

- **统计内核**：`app/market_analysis.py` 以独立 JD 数为主口径，同一
  JD 多实例只计一次 JD 覆盖；importance 双口径（实例级诊断 + JD 级
  must > preferred > mentioned > unknown 归并）；排序稳定；每个
  canonical 携带来源 requirement 与 evidence。本模块补充批次选定
  JD 列表、JD 总数与来源 JD 摘要（报告身份与覆盖率分母）。
- **报告生成**：`app/market_report.py` 纯函数渲染（无模型、无时间戳、
  确定性输出，重复生成内容一致）；章节为样本限制声明 / 报告身份 /
  总览 / 跨 JD 共同要求 / 单 JD 长尾要求 / 证据追溯 / 方法与限制。
  样本限制声明由当前统计动态生成（不写死批次数字）；证据追溯中每个
  来源实例形成独立 Markdown 块（主条目独占一行 + detail 缩进层级 +
  多行 evidence 引用块），特殊字符与多行内容不破坏文档结构；
- **完整性门禁**：生成前复用归并持久化验证（精确 ID 覆盖、mapping
  与来源分区一致、occurrence_count、无重复 mapping、无空 canonical、
  无未知引用——结构合同异常干净拒绝），并检查占位 canonical 名称、
  requirement → extraction → JD 回查、canonical 记录数与有效统计项
  一致、批次 selected_job_ids 全部存在、来源 JD 均在批次范围内；
  任何失败拒绝生成且不覆盖已有报告文件；
- **CLI**：`generate-report --consolidation-id <id> [--output <path>]`，
  完全离线（不读 LLM 配置、无 --execute），默认输出
  `reports/P0-5/market-report-<id>.md`；覆盖已有文件时明确提示。
- **真实报告**：`reports/P0-5/market-report-1.md`（本地私有，含真实
  evidence，不提交）：批次 #1、3 JD、83 实例、72 canonical；高频
  要求团队协作能力（3/3 JD，must 3，4 实例 23/27/53/81）；9 个跨
  JD 共同要求、63 个长尾要求；证据全部可追溯；无占位名、无完整
  JD 正文、无私有路径/密钥/模型响应。
- **公开样例**：`examples/market-report-sample.md`（合成数据，可提交），
  由 `python -m scripts.make_sample_report` 用同一渲染逻辑生成；
  测试断言样例不含真实 JD、密钥、私有路径与模型原始响应。
- **大模块 3（市场统计、证据追溯与 Markdown 报告闭环）已关闭**；
  尚未进入样本扩展阶段。

## 大模块 4：样本扩展（3 → 5）准备（2026-08-05，离线阶段）

- **JD 4、5 资格检查通过**（详细判断见私有记录
  `data/private/module4-eligibility-check.json`，公共仅脱敏结论）：
  均属当前目标岗位范围（AI 应用工程 / LLM 应用开发 / Agent 工程），
  数据完整、要求充分、与 JD 1～3 不重复、构成有效增量样本；扩展
  范围为 JD 1～5；
- **抽取定稿机制**（`scripts/experiments/p0_3/finalize_extraction.py`，
  无模型）：验收脚本报告新增 `manual_review` 区块（approved_run_index /
  approved_result_fingerprint / reviewed_by / reviewed_at），raw 每运行
  记录 run_identifier 与结果指纹；定稿校验报告↔raw 身份（job_id /
  输入指纹 / 版本 / 运行数）、批准运行绑定、幂等安全门（同 JD 同版本
  已有不同结果拒绝、旧格式缺审核元数据拒绝宣称一致）、回读逐项对比；
  8 项测试覆盖（无模型持久化、逐项一致、foreign raw 拒绝、未批准
  拒绝、指纹不一致拒绝、幂等、不同结果拒绝、无 LLM 引用）；
- **归并预检分层采样**（`run_small_scale_precheck`）：由按
  requirement_id 升序取前 N 改为按 JD 分层配额（每 JD 至少 1 条、
  其余按实例数比例分配），保证 JD 4、5 进入预检，输出可审计选样
  摘要；
- **待授权付费阶段**：JD 4/5 抽取验收（多次独立运行）→ 批准运行
  无模型定稿 → 5 JD 归并预检与完整验收 → 增量稳定性比较与新增边界
  人工裁决 → 新批次定稿 → 5 JD 报告与 3→5 对比摘要。

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

1. **样本扩展前验证**：若扩展到 15～20 份 JD，需先分阶段重新走
   抽取验收、归并稳定性分析与人工裁决流程（当前仅证明 3 份 JD
   范围）；
2. 样本扩展后重新生成市场报告（流程不变，报告身份随批次更新）。

## 付费与私有数据依赖

- 付费：抽取（v0.10 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
