# 当前状态

updated_at: 2026-08-07

## 当前可运行功能

| 命令 | 功能 |
|---|---|
| `python -m app.cli import-jds <目录> --use-project-database` | 显式选择项目库并导入 Markdown JD |
| `python -m app.cli extract-jds ... --candidate-output <私有JSON> --execute` | 付费生成 v0.10 + Schema V3 抽取候选，不写正式抽取表 |
| `python -m app.cli finalize-extraction ...` | 从已审核验收产物离线定稿正式抽取 |
| `python -m app.cli consolidate-requirements ... --candidate-output <私有JSON> --execute` | 付费生成归并候选，不写正式归并表 |
| `python -m app.cli finalize-consolidation ...` | 从已审核验收/裁决产物离线定稿正式归并 |
| `python -m app.cli list-* ...` | 列出显式数据库目标中的正式记录 |
| `python -m app.cli audit-extraction-sources ...` | 离线分类正式抽取来源绑定状态 |
| `python -m app.cli audit-consolidation --consolidation-id N ...` | 显示批次脱敏身份和可报告状态 |
| `python -m app.cli validate-consolidation --consolidation-id N ...` | 离线验证真实输入覆盖与持久化一致性 |
| `python -m app.cli generate-report --consolidation-id N ...` | 仅从完成定稿的批次生成 Markdown 报告 |
| `app/market_analysis.py` | 市场统计（实例数、独立 JD 数、importance 分布、来源证据、稳定排序），供下一阶段 `generate-report` 消费 |

验证脚本（均需 `--execute` 才调用付费模型，`--dry-run` 预检不付费）：

- P0-3A 规则场景：`python -m scripts.experiments.p0_3.run_acceptance --execute`
- P0-3B 真实 JD：`python -m scripts.experiments.p0_3.run_real_jd_acceptance --use-project-database --all --execute`
- P0-4 归并验收：`python -m scripts.experiments.p0_4.run_acceptance
  --database-url <实验数据库> --raw-output <私有JSON> --execute`
  （缺省自动选择所选 JD 的唯一共同 v0.10 + Schema V3 抽取版本；查询前
  验证数据库结构；顺序变形合同违规计入 hard gate）
- P0-4 小规模预检：`python -m scripts.experiments.p0_4.run_small_scale_precheck
  --database-url <实验数据库> --execute`

## 当前数据状态（本地私有，不入库提交）

- 数据库已使用现行 Schema 创建（六张业务表，无旧表）；
- 8 份真实 JD 已导入 `data/jd_skill_insight.db`（JD 1～8，重复导入幂等跳过）；
- **已持久化正式抽取结果：JD 1/2/3/4/5/6/7/8**（deepseek-v4-flash、
  `prompt:0.10|schema:3.0`，要求数 37/30/16/27/26/12/43/20，幂等已验证；
  JD 4/5/6/7/8 已绑定完整验收实验身份与来源文件指纹；JD 6/7/8 按
  人工审核批准的 run 0/2/0 完成正式定稿，结果指纹与批准值一致）；
- **已持久化正式归并批次：2 份**：
  - 批次 #1（job_ids=1,2,3，83 条精确覆盖，来源 run-1 + 人工审核决定，
    审核指纹 `51cebada…`、结果指纹 `c5be704e…`，旧候选批次已按
    "不维护旧派生结果"原则删除重建，身份见
    `reports/P0-4/previous-batch-note.json`）；
  - 批次 #2（job_ids=1,2,3,4,5，136 条精确覆盖，deepseek-v4-flash
    `prompt:4.3|schema:3.0`，来源 run-0 + 5 JD 审核决定，审核指纹
    `d7a6942c…`、结果指纹 `edfe2c1a…`；中间候选批次 2（unresolved
    旧语义）身份见 `reports/P0-4/previous-batch-2-note.json`）；
  - 批次 #3（job_ids=1,2,3,4,5,6,7,8，211 条精确覆盖，deepseek-v4-flash
    `prompt:4.3|schema:3.0`，来源 run-2 + 8 JD 人工裁决，审核指纹
    `f93b9394…`、结果指纹 `d6e80729…`、**174 canonical**；8 JD 报告
    `data/private/artifacts/8jd-batch/market-report-3.md`）；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## 正式生产主线

- 所有 CLI 数据库操作必须显式选择 `--database-url` 或
  `--use-project-database`，二者必须且只能选择一个；只读入口不创建缺失的
  SQLite 文件；
- `extract-jds` 与 `consolidate-requirements` 只生成显式私有候选 JSON，
  不写正式抽取或归并表；
- `app/extraction_finalization.py` 与 `app/consolidation_finalization.py`
  集中执行审核身份、输入/结果指纹、精确覆盖、幂等冲突和原子写入门禁；
- `generate-report` 要求归并批次至少绑定审核决定指纹和来源运行标识；结构
  合法但未经定稿的批次拒绝生成报告；
- 当前批次 #2 的脱敏身份检查通过：job_ids=1～5、136 mappings、97
  canonical、结果指纹 `edfe2c1a…`、审核决定指纹 `d7a6942c…`、来源
  run-0，属于可报告正式批次；
- 当前正式抽取来源绑定离线分类：JD 4/5/6/7/8 为 `fully_bound`；JD 1/2/3 的
  数据库记录没有当前定稿合同所需的验收/审核/文件指纹，机器分类为
  `unverified`，继续使用既有历史豁免（见下方 P0-7 关闭记录）。文档保留
  其既有人工审计结论；不回填、不重跑、不将其宣称为 `fully_bound`；
- **P0-7 已关闭（2026-08-07）**：正式生产机制全部完成；JD 1/2/3 按
  项目级历史风险接受记录豁免——`reports/P0-7/legacy-extraction-waiver.json`
  （批准人 project-owner；仅限 JD 1/2/3 历史记录、仅供当前 MVP 归并/
  统计/报告；新增 JD 禁止使用）。**例外不等于完整来源绑定**：JD 1/2/3
  分类保持 `unverified`，报告生成继续显式标注 provenance 风险，不因
  豁免记录隐藏或删除；新增 JD 必须全部走现行正式主线；
- 合成端到端测试覆盖：模型候选不进入正式表，审核定稿后正式抽取/归并
  才出现，并可进入市场统计和报告门禁；
- **接口收口（2026-08-07）**：
  - 验收产物合同统一（两轮）：run_real_jd_acceptance 的 report/raw
    共用单一 identity（run_identifier/model/prompt_version/schema_version/
    job_ids/jd_set_fingerprint/runs/max_attempts）；finalize-extraction
    校验定稿 JD ∈ 整轮 job_ids 且 report/raw 的 JD 集合、runs、
    max_attempts、jd_set_fingerprint 一致，批量验收产物可逐 JD 定稿；
    verify_extraction_source 对新格式同合同（旧格式向后兼容）；
  - 候选产物定位明确为**单次预检产物**：extract-jds / consolidate-
    requirements 候选不进入正式链路，finalize 只消费完整验收产物；
    README/ARCHITECTURE 已同步；
  - 归并定稿门禁加强：正式归并必须带完整 7 字段审核元数据（审核人/
    时间/批准运行/批准结果指纹/审核决定指纹/最终结果指纹），
    final_result_fingerprint 必须与当前持久化结果一致；旧批次 #1/#2
    已用 backfill_consolidation_metadata.py 离线补齐（不改结果）；
  - 报告门禁增加上游 provenance 标注：generate-report 检查批次来源
    抽取绑定状态，存在 unverified/reviewed_unbound 时报告与方法节
    显式标注风险（不阻塞生成）；JD 1/2/3 按 P0-7 项目级历史风险豁免
    （`reports/P0-7/legacy-extraction-waiver.json`）在报告中显式引用
    （仅供当前 MVP 归并/统计/报告消费，豁免不等于 fully_bound），
    风险标注保留、不因豁免隐藏；audit-consolidation 输出
    extraction_source_status；
  - E2E 真实化：test_pipeline_e2e 调用 run_real_jd_acceptance /
    run_acceptance / apply_review_decisions 真实链路（仅人工审核步骤
    模拟），终点为真实 generate-report 生成 Markdown 报告；不再手工
    构造中间 JSON；候选命令断言为预检落盘。

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
- **范围声明**：当前证明 5 份 JD（136 条实例）范围的归并质量与
  3→5 增量稳定性（旧 83 条回归全部保持）；15～20 份 JD 扩展仍需
  分阶段验证（新增 JD 会引入新的边界对，需重新走稳定性分析 +
  人工裁决流程）。

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
- **真实报告**：早期批次报告（批次 #1 的 3 JD 报告、批次 #2 的 5 JD
  报告）为可再生派生产物，已随产物清理删除（`generate-report` 可随时
  重建）；当前有效报告为批次 #3 的 8 JD 报告（8 JD、211 实例、
  174 canonical、团队协作 5/8 JD），属私有材料，与验收/裁决产物一并
  归档于 `data/private/artifacts/8jd-batch/`。
- **公开样例**：`examples/market-report-sample.md`（合成数据，可提交），
  由 `python -m scripts.make_sample_report` 用同一渲染逻辑生成；
  测试断言样例不含真实 JD、密钥、私有路径与模型原始响应。
- **大模块 3（市场统计、证据追溯与 Markdown 报告闭环）已关闭**；
  尚未进入样本扩展阶段。

## 大模块 4：样本扩展（3 → 5）收口（2026-08-06，离线完成）

- **JD 4、5 资格检查通过**（详细判断见私有记录
  `data/private/module4-eligibility-check.json`，公共仅脱敏结论）：
  均属当前目标岗位范围（AI 应用工程 / LLM 应用开发 / Agent 工程），
  数据完整、要求充分、与 JD 1～3 不重复、构成有效增量样本；
- **抽取定稿绑定完整验收实验**（`finalize_extraction.py`，无模型）：
  report 顶层 passed=true 且顶层 hard gate 为空；expected_runs ==
  successful_runs、failed_runs == 0、raw 中该 JD 运行记录数与
  expected_runs 完全一致；report 与 raw 共享整轮 acceptance run
  identity（run_identifier/model/prompt/schema/job_ids），局部运行名
  跨实验不可混用；身份字段缺失明确拒绝（不填充默认值）；正式抽取
  记录绑定来源 report/raw 文件指纹（sha256）与整轮验收身份；事务
  原子性：写入并 flush → 回读重建 → 比较完整结果指纹 → 校验审核
  元数据 → commit，任何失败 rollback 保持定稿前状态；幂等安全门
  要求结果、批准运行、来源实验、审核身份与文件指纹全部一致；
  14 项测试覆盖；
- **JD 4/5 离线来源复核**：现有正式抽取（ID 4/5）与验收报告、raw
  逐项核对通过（结果指纹一致、审核元数据完整、文件指纹可复算）；
- **归并预检分层采样**（`run_small_scale_precheck`）：按 JD 分层配额
  （每 JD 至少 1 条、其余按实例数比例分配），保证 JD 4、5 进入预检，
  输出可审计选样摘要；
- **增量比较以正式批次为真值**（`compare_incremental.py`）：旧范围
  唯一真值 = 正式批次 #1 的 expected requirement IDs（外部 ID 文件
  仅作校验输入，不等即拒绝）；新 raw 校验 selected_job_ids、输入
  指纹、抽取器版本与数据库当前输入一致；每个观察（3 独立 + 顺序
  变形）执行完整合同 + 精确 ID 校验（coverage=100%、结构违规=0、
  精确覆盖全部 136 条），任一不合格拒绝分析不静默跳过；singleton
  吸收统计不依赖实例 ID 大小；私有分析含完整成员、raw_name、来源
  JD、evidence、pair 保持率、distinct job count、命中旧裁决与变化
  类型；
- **unresolved 确定性语义**：unresolved 不再默认拆成全部 singleton，
  必须显式提供目标分组（`groups`：组内合并、组间拆开）或
  `preserve_source=true`（保留来源分区），结构不完整拒绝应用；
- **5 JD 完整归并验收**（136 实例，3 独立 + 顺序变形）：hard gate=0、
  顺序变形合同通过；Jaccard 39~67% 为诊断指标；
- **原始模型观察 vs 最终裁决分开比较**：
  - 模型原始运行（3 独立 + 顺序变形）：旧 14 对中 9 对被拆开、
    11 个旧 singleton 被吸收、10 个新合并对、5 次新增实例扩员、
    must-link 破坏 8 个、cannot-link 破坏 0；
  - 最终人工裁决结果限制到旧 83 条：**旧对 0 破坏、旧 must-link
    0 破坏、旧 cannot-link 0 破坏、无未授权合并**；10 个新增合并
    对全部有裁决记录（62-64/65-67/75-77 与 19-20 为同句 any_of
    等价替代重新分类为 must-link，19-20 由第二轮回归发现补裁决）；
  - 审核决定 `review-decisions-5jd.json`（指纹 `d7a6942c…`）：旧
    裁决全部维持，新增 must-link 12 组（含 3 个 any_of 等价替代组
    与 19-20）；
- **最终正式批次**：**id=2**（job_ids=1,2,3,4,5、136 条精确覆盖、
  **97 canonical**、结果指纹 `edfe2c1a…`、审核指纹 `d7a6942c…`、
  来源 run-0）；与旧批次 id=1 并存（增量基线）；离线一致性验证
  NONE、重复定稿幂等；中间候选批次（unresolved 旧语义，103
  canonical、指纹 b87d2563…）已按"不维护错误派生数据"原则保存
  身份后删除；
- **5 JD 报告**：批次 #2 报告（5 JD、136 实例、97 canonical、团队
  协作 4/5 JD、跨 JD 共同 19、长尾 78）为可再生派生产物，已随产物
  清理删除（`generate-report --consolidation-id 2` 可重建）；
- **3→5 对比摘要** `reports/P0-4/module4-3to5-comparison.json`：
  共同要求 9→19、长尾 63→78、团队协作 3/3→4/5；只描述扩展影响，
  不解释为市场趋势；
- **是否进入 6～8 JD**：新增抽取无系统性缺陷、验收可原样定稿、
  归并合同通过、旧核心关系经裁决保持稳定、新增边界可人工审核、
  输入 136 条成本可接受、报告无结构问题——满足进入条件，但按模块
  边界**不自动扩展**，等待下一模块指令。

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

1. **8 JD 批次已正式关闭**（批次 #3 定稿 + 8 JD 报告生成，provenance
   文案已修正为引用 P0-7 豁免记录）；进入 **8 → 12 JD 扩样**：用户
   提供新增 4 份真实 JD → 导入 → 抽取验收 → 人工审核 → finalize
   extraction → 全量归并验收 → 必要人工裁决 → finalize consolidation
   → 报告；实测抽取成本、全量归并规模与人工裁决量，无真实阻塞再增到
   15 JD（固定终点，不扩展到 20）；新增 JD 禁止使用 JD 1/2/3 的历史
   豁免；
2. 每批只执行现有正式主线；付费调用前汇报模型、本批 JD 数量与 ID、
   调用目的、预计命令，等待授权后执行。

## 付费与私有数据依赖

- 付费：抽取（v0.10 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
