# 当前状态

updated_at: 2026-08-24

## 生命周期与范围

v0.1 高保证验收基线已完成并冻结。现行 MVP 是本地一键 CLI 数据分析流水线：

```text
Markdown JD 目录 → analyze-jds --execute
→ 两段式抽取与自动硬门 → 系统策略定稿
→ 单次保守归并与自动硬门 → 系统策略定稿
→ 独立 JD 统计 → 原文证据追溯 → Markdown 报告
```

日常运行不需要人工审核。多次 acceptance、稳定性分析、人工规则/cluster 审计与文件型
finalize 保留为开发者修改模型、Prompt、Schema 或规则后的离线评测工具。单次 candidate
仍是可选私有预检。当前没有获授权的后续实施阶段；默认只维护现有闭环和修复明确缺陷。

项目不提供 Web UI、在线服务、简历匹配、ATS、Agent 编排或 RAG 服务，不维护旧抽取
版本、旧 Schema、旧数据库结构和已删除的层级关系。

## 当前软件基线

- 正式抽取合同为 v0.10 + Schema V3；
- 使用者入口按输入指纹创建私有隔离运行空间、SQLite 数据库、artifact、摘要和报告；
- 抽取与归并均采用单次生成、现有有限纠错和机器 hard gates，通过后由 `auto-v1`
  系统策略调用共用正式化核心；
- 同一输入已完整完成时直接复用，不重复调用模型；失败摘要记录停止阶段；
- requirement instance 保留 JD 事实与 evidence，canonical requirement 只承担跨 JD 统计；
- 频率以独立 JD 数为主，同一 JD 的重复实例只贡献一次覆盖；
- Markdown 报告由已定稿归并结果确定性生成，并逐项回查来源证据；
- 自动化测试使用 fake 客户端、临时文件和临时数据库，不调用付费模型；
- 公开 sample 使用虚构数据与正式统计、渲染代码生成。

### 匿名工程验收事实

- 私有验收范围为 15 份真实 JD；
- 正式结果包含 409 requirement instances 和 329 canonical requirements；
- consolidation coverage 为 100%，structural violations 为 0，最终结果满足报告门禁；
- 该 v0.1 历史验收批次经过人工 must-link/cannot-link 与名称裁决，并使用 frozen-base
  约束；这些是历史验收事实，不是现行一键主线的运行要求；
- 跨运行 positive-pair Jaccard 最低观察值为 28.85%。该值是实际稳定性 limitation，
  也是诊断指标而非结构 hard gate；不能据此把单次模型归并解释为市场事实。

## 报告呈现

报告提供完整频率榜与准备优先级榜，后者按必需篇数、加分篇数、总出现篇数依次降序。
主表显示中文重要性计数与任选标注，末尾折叠来源和证据，不展示内部运行身份。

## 当前安全门

- 付费调用必须显式 `--execute`；开发者数据库入口必须显式选择目标；
- `analyze-jds` 的数据库目标由输入指纹确定并隔离在 Git 忽略的私有运行目录，不接受
  隐式项目数据库写入；
- 单次 candidate 只写新建的私有 JSON，不写模型生成的正式抽取或归并数据；
- 自动抽取只在整批 JD 全部通过 Schema、分句覆盖、逻辑组和 evidence 存在性检查后
  原子写入；任一 JD 失败时不留下正式抽取；
- 自动归并重新验证输入指纹、精确 requirement ID 覆盖、唯一映射、结构合同和占位名称；
  失败时不留下正式归并或报告；
- 文件型 extraction/consolidation finalize 继续校验完整 acceptance、人工批准/裁决、
  当前数据库输入、结果/文件指纹和结构合同；
- 重复正式化只有在身份、内容和批准绑定完全一致时才幂等跳过；
- 报告入口重新验证归并定稿身份、结果 fingerprint、mapping/partition、
  requirement → extraction → JD 回查和上游 provenance；
- 历史来源缺少现行机器绑定时，只有私有、范围受限的结构化 waiver 能放行报告，
  风险提示仍须保留，新增数据不得继承该例外；
- 非当前版本、Schema 或数据库结构明确拒绝，不兼容、不迁移、不自动删除。

模型生成的正式抽取与归并数据只允许由共用正式化写入核心持久化；一键系统策略与文件型
人工 finalize 入口均复用该核心。JD 导入、公开 sample 临时数据库和其他非模型业务写入
不受这句话约束。

## 公开与私有边界

正式链已在本地私有数据集上完成端到端验收。以下内容保留在被 Git 忽略的私有目录或
本地数据库中，不作为公开项目事实维护：

- 真实 JD、公司信息和个人筛选材料；
- 模型原始响应、acceptance 产物、人工裁决与范围受限 waiver；
- 逐记录来源状态、具体 waiver 内容、artifact 路径与身份指纹；
- 岗位要求排行、共同要求、长尾分布等具体市场结论；
- 正式数据库、真实报告及其发布清单。

公开仓库只证明代码合同、测试覆盖和合成 sample 的可复现性，不声称公开复现私人批次。
LLM 抽取与归并仍存在随机性。一键结果通过的是当前机器合同，不等同于人工语义审计；
开发者 acceptance 用于在改变模型或规则时重新验证这一自动边界。

## 当前入口索引

- 一键使用入口与最短路径：[README.md](../README.md)；
- 抽取验收：`scripts.experiments.p0_3.run_acceptance`、
  `scripts.experiments.p0_3.run_real_jd_acceptance`；
- 归并验收与裁决：`scripts.experiments.p0_4.run_acceptance`、
  `analyze_stability`、`apply_review_decisions`；
- 模块边界：[ARCHITECTURE.md](ARCHITECTURE.md)；
- 抽取规则：[EXTRACTION_RULES.md](EXTRACTION_RULES.md)；
- 验证合同：[VALIDATION.md](VALIDATION.md)。
