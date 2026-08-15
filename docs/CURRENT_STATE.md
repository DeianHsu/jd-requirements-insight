# 当前状态

updated_at: 2026-08-15

## 生命周期与范围

v0.1 MVP 已完成并冻结。当前仓库是本地 CLI 数据分析流水线，维护范围为：

```text
JD 导入
→ 抽取 acceptance → 人工审核 → finalize-extraction
→ 归并 acceptance / 稳定性分析 → 人工裁决 → finalize-consolidation
→ 独立 JD 统计 → 原文证据追溯 → Markdown 报告
```

单次 extract/consolidate candidate 是可选私有预检，不属于正式链，也不作为 finalize
输入。当前没有获授权的后续实施阶段。

项目不提供 Web UI、在线服务、简历匹配、ATS、Agent 编排或 RAG 服务，不维护旧抽取
版本、旧 Schema、旧数据库结构和已删除的层级关系。

## 当前正式数据快照

本地私有数据库 `data/jd_skill_insight.db` 使用现行六表结构，包含 15 份真实 JD。
真实 JD、数据库、模型原始响应、人工裁决和真实报告不提交 Git。

### 抽取

- JD 1～15 均有 v0.10 + Schema V3 正式抽取；
- requirement instances 共 409；
- JD 1～3 的来源状态为 `unverified`；
- JD 4～15 的验收、人工审核、批准运行及 report/raw 身份绑定完整，状态为
  `fully_bound`。

### 归并与报告

当前最终批次为 consolidation #5：

- selected job IDs：1～15；
- extractor：`deepseek-v4-flash|prompt:0.10|schema:3.0`；
- consolidator：`deepseek-v4-flash|prompt:4.3|schema:3.0`；
- 409 requirement instances、329 canonical requirements、409 mappings；
- coverage=100%，结构违规=0，`reportable=True`；
- 43 个跨 JD 共同要求、286 个单 JD 长尾要求；
- 覆盖最高为团队协作能力：9/15 JD、10 instances；
- positive-pair Jaccard 最低观察值为 28.85%；
- 正式结果经人工 must-link/cannot-link、名称裁决和 frozen-base 约束。

最终私有报告位于 `data/private/artifacts/15jd-batch/market-report-5.md`，可由同一批次
确定性重建。

## 当前安全门

- 付费调用必须显式 `--execute`；数据库目标必须显式选择；
- 单次 candidate 只写新建的私有 JSON，不写正式抽取/归并表；
- extraction finalize 校验完整 report/raw、运行身份、人工批准和结果/文件指纹；
- consolidation finalize 校验验收身份、批准 source run、审核决定、当前数据库输入
  fingerprint、精确 requirement ID 覆盖和结构合同；
- 重复 finalize 只有在身份、内容和审核绑定完全一致时才幂等跳过；
- 报告入口重新验证归并定稿身份、结果 fingerprint、mapping/partition、
  requirement → extraction → JD 回查和上游 provenance；
- 非当前版本、Schema 或数据库结构明确拒绝，不兼容、不迁移、不自动删除。

模型生成的正式抽取与归并数据只允许由两个 finalize 入口写入；JD 导入、公开 sample
临时数据库和其他非模型业务写入不受这句话约束。

## 当前例外与限制

- JD 1～3 缺少现行机器可验证 provenance，只能在
  `reports/P0-7/legacy-extraction-waiver.json` 精确限定的当前 MVP 范围内消费；
  报告必须保留风险提示，新 JD 不得继承；
- 归并稳定性指标不理想：最低 Jaccard 28.85%。它是诊断指标，正式结果依靠完整结构门禁、
  人工裁决和 frozen-base，不应把单次模型运行解释为市场事实；
- 15 JD 只证明工程闭环，不代表行业排名或完整市场结论；
- 真实数据因隐私不公开，公开 clone 不能逐字复算真实报告；
- 来源状态 `reviewed_unbound` 的命名偏宽，是非阻塞展示卫生项，不属于 accepted
  exception。

前两项为何不阻塞 v0.1 关闭，见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## 当前入口索引

- 正式 CLI 与最短使用路径：[README.md](../README.md)；
- 抽取验收：`scripts.experiments.p0_3.run_acceptance`、
  `scripts.experiments.p0_3.run_real_jd_acceptance`；
- 归并验收与裁决：`scripts.experiments.p0_4.run_acceptance`、
  `analyze_stability`、`apply_review_decisions`；
- 模块边界：[ARCHITECTURE.md](ARCHITECTURE.md)；
- 验证合同：[annotation/VALIDATION.md](annotation/VALIDATION.md)。

自动化测试使用 fake 客户端、临时文件和临时数据库，不调用付费模型。公开 sample 使用
虚构数据与正式统计/渲染代码生成 `examples/market-report-sample.md`。
