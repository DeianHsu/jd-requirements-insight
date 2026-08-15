# 当前状态

updated_at: 2026-08-15

## 当前范围

JD Requirements Insight 是本地 CLI 数据分析流水线，唯一主线为：

```text
JD 导入
→ v0.10 + Schema V3 两段式抽取与质量验证
→ 人工审核后定稿 requirement instances
→ requirement instances 归并为 canonical requirements
→ 稳定性分析与人工裁决
→ 归并定稿
→ 独立 JD 统计、原文证据追溯与 Markdown 报告
```

项目不提供 Web UI、在线服务、简历匹配、ATS、Agent 编排或 RAG 服务，不维护旧抽取
版本、旧 Schema、旧数据库结构和已删除的层级关系。

## 当前可运行入口

| 命令 | 当前职责 |
|---|---|
| `python -m app.cli import-jds <目录> ...` | 显式选择数据库并导入 Markdown JD |
| `python -m app.cli extract-jds ... --candidate-output <私有JSON> --execute` | 付费生成单次抽取候选，不写正式抽取表 |
| `python -m scripts.experiments.p0_3.run_acceptance --execute` | 运行规则场景与确定性变形验收 |
| `python -m scripts.experiments.p0_3.run_real_jd_acceptance ... --execute` | 对真实 JD 运行多次抽取验收 |
| `python -m app.cli finalize-extraction ...` | 从完整验收产物离线定稿正式抽取 |
| `python -m app.cli consolidate-requirements ... --candidate-output <私有JSON> --execute` | 付费生成单次归并候选，不写正式归并表 |
| `python -m scripts.experiments.p0_4.run_acceptance ... --execute` | 运行多次归并、顺序变形和稳定性验收 |
| `python -m scripts.experiments.p0_4.analyze_stability ...` | 离线生成跨运行稳定性与人工审核材料 |
| `python -m scripts.experiments.p0_4.apply_review_decisions ...` | 离线应用 must-link、cannot-link、名称 override 与 frozen-base |
| `python -m app.cli finalize-consolidation ...` | 从已审核裁决产物离线定稿正式归并 |
| `python -m app.cli audit-extraction-sources ...` | 只读分类正式抽取来源绑定状态 |
| `python -m app.cli audit-consolidation --consolidation-id N ...` | 只读显示归并批次身份与可报告状态 |
| `python -m app.cli validate-consolidation --consolidation-id N ...` | 离线验证真实输入覆盖与持久化一致性 |
| `python -m app.cli generate-report --consolidation-id N ...` | 从完整定稿批次生成确定性 Markdown 报告 |

所有数据库命令必须且只能显式选择 `--database-url` 或 `--use-project-database`；
付费模型调用必须显式提供 `--execute`。候选 JSON 不属于正式数据，正式业务表只允许
`finalize-extraction` 和 `finalize-consolidation` 写入。

## 当前正式数据

本地私有数据库 `data/jd_skill_insight.db` 使用现行六表 Schema，包含 15 份真实 JD。
真实 JD、数据库、模型原始响应、人工裁决和真实报告均不提交 Git。

### 抽取

- JD 1～15 均有 v0.10 + Schema V3 正式抽取；
- requirement instances 数依次为
  37/30/16/27/26/12/43/20/36/8/24/21/31/64/14，合计 409；
- JD 4～15 的验收、审核、批准运行、结果指纹及 report/raw 文件指纹绑定完整，
  来源状态为 `fully_bound`；
- JD 1～3 产生于现行绑定合同建立前，机器状态保持 `unverified`，不回填、不重跑，
  仅由 P0-7 waiver 限域供当前 MVP 消费。

### 归并与报告

当前最终批次为 consolidation #5：

- selected job IDs：1～15；
- extractor：`deepseek-v4-flash|prompt:0.10|schema:3.0`；
- consolidator：`deepseek-v4-flash|prompt:4.3|schema:3.0`；
- 409 requirement instances、329 canonical requirements、409 mappings；
- coverage=100%，结构违规=0，`reportable=True`；
- source run=`run-1`；
- final result fingerprint=`17d087e8…172c2`；
- review-decisions fingerprint=`165be7ba…c46e8`；
- 43 个跨 JD 共同要求、286 个单 JD 长尾要求；
- 覆盖最高为团队协作能力：9/15 JD、10 instances。

批次 #5 使用已定稿 12 JD 批次作为 frozen base：旧范围 IDs 1～300 的 partition、
canonical ID 和 canonical name 不允许被增量裁决改写；新增 IDs 301～409 经显式人工
must-link / cannot-link 与名称 override 后定稿。最终私有报告位于
`data/private/artifacts/15jd-batch/market-report-5.md`，可由同一批次确定性重建。

## 正式安全门

- 抽取与归并候选只写显式私有 JSON，不写正式表；
- extraction finalize 校验整轮 report/raw 身份、运行完整性、人工批准、结果及文件指纹，
  回读不一致时回滚；
- consolidation finalize 校验验收身份、批准 source run、审核决定、最终结果、当前数据库
  输入 fingerprint、精确 requirement ID 覆盖和结构合同；
- 重复 finalize 只有在身份、内容和审核绑定完全一致时才幂等跳过；
- 报告入口重新验证归并定稿元数据、最终结果 fingerprint、mapping/partition 一致性、
  requirement → extraction → JD 回查以及上游抽取 provenance；
- P0-7 waiver 只允许
  `reports/P0-7/legacy-extraction-waiver.json` 明确列出的 JD 1～3 用于当前 MVP
  `generate-report` 链路；新增 JD 不得继承；
- 旧版本、旧 Schema 或旧数据库结构不兼容、不迁移、不自动删除：备份原始 JD，删除旧
  派生数据库并使用当前版本重新生成。

## 验证与公开复现

- 自动化测试使用 fake 客户端、临时数据库和临时文件，不调用付费模型；
- 正式 CLI E2E 覆盖导入、完整抽取验收、人工审核模拟、抽取定稿、完整归并验收、裁决、
  归并定稿、统计和报告；
- `scripts.make_sample_report` 使用虚构数据与正式统计/渲染代码生成
  `examples/market-report-sample.md`，不读取私有数据；
- 真实 15 JD 因隐私不公开，公开 clone 不能逐字复算真实报告，但可以复现 Schema、合同、
  安全门、合成 E2E 和报告形态。

## 已知限制与当前边界

- 15 JD 只证明 MVP 工程闭环，不代表行业排名或完整市场结论；
- 归并 positive-pair Jaccard 最低观察到 28.85%，属于诊断指标；正式结果依靠人工裁决和
  frozen-base 保证，不应把单次模型运行直接解释为市场事实；
- JD 1～3 缺少现行机器可验证 provenance，waiver 不等于 `fully_bound`，报告必须保留
  风险提示；
- 来源状态 `reviewed_unbound` 的命名仍偏宽，只是非阻塞展示问题；
- 15 JD 是 v0.1 MVP 固定终点，版本为 `0.1.0`，release tag 为 `v0.1.0-mvp`；
- 当前没有获授权的后续实施阶段。新增工作应单独确认范围，新 JD 必须全部走现行正式主线。
