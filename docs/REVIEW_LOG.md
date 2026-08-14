# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：15 JD order transformation blocker 调查（2026-08-14）

### 根因与性质

- 15 JD acceptance 的 3 个 independent runs 均成功且各自精确覆盖 409 IDs、
  coverage=100%、结构违规=0，结果指纹均可复算；run1 及审核后的 329 canonical
  final candidate 未发现 correctness blocker。
- order transformation 使用固定种子 `20260803` 打乱同一 409 条输入，调用同一
  deepseek-v4-flash / Prompt 4.3 / Schema 3.0；三次有限重试均返回非法 JSON，最终
  错误位于 line 1852 / column 34。raw 只保留失败原因，没有可验证的 order result。
- 因此这是 **metamorphic diagnostic execution failure**，不是已选 source run 或 final
  candidate 的结构/覆盖失败；但它使正式 acceptance 缺少一次成功的顺序变形观察。

### 当前正式 gate 路径

- `run_acceptance.py` 明确把 order 聚类失败、coverage 不足或结构违规加入顶层
  `hard_gate_failures`；order positive-pair Jaccard <85% 才只记 warning。该规则由
  2026-08-03 的 `168de0c` 明确引入，代码注释、VALIDATION 文档和定向测试一致，
  属于有意设计，不是 finalize 的历史偶然行为。
- `finalize_consolidation()` 在读取 report 后首先检查顶层 hard gate，非空即拒绝；
  没有按失败类型降级、人工批准或 exception 的消费分支。
- 当前 runner 只有完整 `runs + order` 模式，不能读取既有 raw 复用三个 successful
  runs；没有 order-only retry。仓库唯一结构化 waiver 是 P0-7 历史 extraction waiver，
  仅服务指定旧 JD 的归并/统计/报告 provenance，不覆盖 P0-4 acceptance hard gate。

### 推荐最小解决方案

- 推荐增加 **order-only resume**，不修改 hard-gate 语义：强校验既有 report/raw、
  当前数据库 input fingerprint、selected jobs、模型/Prompt/Schema、三个 source run
  的完整结果及指纹，并要求原报告仅存在一个 order execution failure；按原 seed 重建
  shuffled input，只调用 order transformation，输出新的完整 report/raw，原产物不覆盖。
- 模型仍为 deepseek-v4-flash，Prompt 4.3，Schema 3.0，`max_attempts=3`；预计 1 次、
  最多 3 次付费请求。成功结果继续执行 coverage/结构 hard gate，Jaccard 继续只作 warning。
- 不推荐当前把 execution failure 改成非阻塞 exception：这会改变明确的 P0-4 合同并
  放弃唯一的输入顺序观察，且仓库没有对应的结构化批准与 finalize 消费机制。若专用重试
  再次耗尽，才应由外部 Reviewer 另行决定是否设计严格限定的 exception。

### 当前状态

本轮只读调查，未改代码、未调用模型、未修改 acceptance/candidate/decisions、未 finalize。
当前 hard gate 仍为 1。另：acceptance report 的 `manual_cluster_review` 批准字段目前为空；
order gate 修复后，还需把已完成的外部 Review 写入现有字段，finalize 才能继续。
