# 项目计划

## 当前定位

v0.1 MVP 已完成并冻结。项目已具备以下可演示、可评测闭环：

- Markdown JD 导入与去重；
- v0.10 + Schema V3 两段式抽取及规则场景、真实 JD 验收；
- 人工审核绑定后的抽取定稿；
- requirement instance → canonical requirement 唯一归并与稳定性分析；
- 人工裁决绑定后的归并定稿；
- 独立 JD 统计、原文证据追溯和确定性 Markdown 报告；
- 显式数据库目标、付费调用确认、候选隔离、幂等与失败回滚门禁。

当前没有获授权的后续实施阶段。默认工作仅限维护该闭环、修复明确缺陷和保持公开
复现；扩充样本、增加功能或进入下一版本必须另行授权。

当前正式数据、状态分类和实测指标以 [CURRENT_STATE.md](CURRENT_STATE.md) 为准。

## v0.1 关闭决策中的 accepted exceptions

以下两项影响 v0.1 正式结果的解释，但不推翻阶段关闭决定。

### 1. JD 1～3 抽取 provenance

JD 1～3 的正式抽取产生于现行验收身份绑定合同建立之前，不能宣称
`fully_bound`。v0.1 允许关闭，是因为结构化 waiver 将使用范围严格限制为当前
MVP 的既有 JD 和报告链路，并明确：

- 机器分类保持 `unverified`；
- 不回填指纹，不重新包装为现行验收产物；
- 新增 JD 不得继承该例外；
- 报告必须披露上游 provenance 风险。

约束文件：`reports/P0-7/legacy-extraction-waiver.json`。

### 2. 归并稳定性

跨运行 positive-pair Jaccard 是诊断指标，不是覆盖与结构合同的 hard gate。v0.1
允许在该指标未达到理想水平时关闭，是因为正式结果经过完整覆盖检查、结构检查、人工
must-link/cannot-link 与名称裁决，并以 frozen base 防止已批准分区被后续扩样改写。

该决定不表示单次模型归并足以成为市场事实；未来若扩大样本，应重新评估归并 Prompt、
确定性归一化与人工裁决成本。

## 维护边界

- 新 JD 必须完整经过当前 acceptance → review → finalize 正式链；
- 单次 candidate 只用于可选预检，不作为 finalize 输入；
- 不维护旧抽取版本、旧 Schema、旧数据库结构或已删除功能；
- 非阻塞命名、展示和文档卫生问题不编号为 accepted exception，修复后直接删除记录；
- 当前事实变化更新 CURRENT_STATE；本文件只在关闭决策或获授权阶段变化时更新。

安全、隐私、验证、提交与推送约束见 [AGENTS.md](../AGENTS.md)。
