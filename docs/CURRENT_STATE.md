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
输入。当前没有获授权的后续实施阶段；默认只维护现有闭环和修复明确缺陷。

项目不提供 Web UI、在线服务、简历匹配、ATS、Agent 编排或 RAG 服务，不维护旧抽取
版本、旧 Schema、旧数据库结构和已删除的层级关系。

## 当前软件基线

- 正式抽取合同为 v0.10 + Schema V3；
- 正式链为 acceptance → human review → finalize，单次 candidate 不能替代 acceptance；
- requirement instance 保留 JD 事实与 evidence，canonical requirement 只承担跨 JD 统计；
- 频率以独立 JD 数为主，同一 JD 的重复实例只贡献一次覆盖；
- Markdown 报告由已定稿归并结果确定性生成，并逐项回查来源证据；
- 自动化测试使用 fake 客户端、临时文件和临时数据库，不调用付费模型；
- 公开 sample 使用虚构数据与正式统计、渲染代码生成。

## 当前安全门

- 付费调用必须显式 `--execute`；数据库目标必须显式选择；
- 单次 candidate 只写新建的私有 JSON，不写模型生成的正式抽取或归并数据；
- extraction finalize 校验完整 report/raw、运行身份、人工批准和结果/文件指纹；
- consolidation finalize 校验验收身份、批准 source run、审核决定、当前数据库输入
  fingerprint、精确 requirement ID 覆盖和结构合同；
- 重复 finalize 只有在身份、内容和审核绑定完全一致时才幂等跳过；
- 报告入口重新验证归并定稿身份、结果 fingerprint、mapping/partition、
  requirement → extraction → JD 回查和上游 provenance；
- 历史来源缺少现行机器绑定时，只有私有、范围受限的结构化 waiver 能放行报告，
  风险提示仍须保留，新增数据不得继承该例外；
- 非当前版本、Schema 或数据库结构明确拒绝，不兼容、不迁移、不自动删除。

模型生成的正式抽取与归并数据只允许由两个 finalize 入口写入；JD 导入、公开 sample
临时数据库和其他非模型业务写入不受这句话约束。

## 公开与私有边界

正式链已在本地私有数据集上完成端到端验收。以下内容保留在被 Git 忽略的私有目录或
本地数据库中，不作为公开项目事实维护：

- 真实 JD、公司信息和个人筛选材料；
- 模型原始响应、acceptance 产物、人工裁决与范围受限 waiver；
- 精确批次规模、统计指标、稳定性观察值和市场结论；
- 正式数据库、真实报告及其发布清单。

公开仓库只证明代码合同、测试覆盖和合成 sample 的可复现性，不声称公开复现私人批次。
LLM 抽取与归并仍存在随机性，正式结果必须经过验收和人工审核。

## 当前入口索引

- 正式 CLI 与最短使用路径：[README.md](../README.md)；
- 抽取验收：`scripts.experiments.p0_3.run_acceptance`、
  `scripts.experiments.p0_3.run_real_jd_acceptance`；
- 归并验收与裁决：`scripts.experiments.p0_4.run_acceptance`、
  `analyze_stability`、`apply_review_decisions`；
- 模块边界：[ARCHITECTURE.md](ARCHITECTURE.md)；
- 验证合同：[annotation/VALIDATION.md](annotation/VALIDATION.md)。
