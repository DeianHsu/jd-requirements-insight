# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：15 JD order-only resume（2026-08-14）

- 根因：15 JD acceptance 的三个 independent runs 已成功，但固定种子的 order
  transformation 在三次有限重试后仍返回非法 JSON；原 runner 只能整批重跑，无法安全
  复用已有 independent runs。该失败仍按既有合同保持 hard gate，不作豁免或降级。
- `run_acceptance.py` 新增最小 order-only resume 入口：读取原 report/raw，强校验当前
  数据库 input fingerprint、JD 范围、模型/Prompt/Schema、三个 independent runs 的
  身份、结果指纹、精确覆盖和结构合同，并仅接受单一 order execution hard gate。
  校验完成后按原 seed `20260803` 只重试 order transformation，使用新路径输出完整
  report/raw，拒绝覆盖原产物；三个 independent runs 原样复用，不重新调用。
- gate 语义未变：order execution failure、coverage 不足、结构违规仍进入 hard gate；
  positive-pair Jaccard 低于 85% 仍只记 warning。现有 `manual_cluster_review` 已包含
  reviewer、时间、approved run/result fingerprint、结论与备注，足以记录已完成的外部
  Review，无需扩展数据模型。
- 定向验证：acceptance 与 finalize 人工审核字段相关测试共 25 项通过；Ruff 通过。
  真实 15 JD 原产物只读身份预检通过：JD1～15 / 409 instances、三个 source run 及
  指纹均匹配。未调用 LLM、未修改 final candidate/review-decisions、未 finalize。
- 当前仅剩付费执行 order-only resume：deepseek-v4-flash / Prompt 4.3 / Schema 3.0，
  预计 1 次、最多 3 次请求。成功后还需把已完成的外部 Review 写入现有
  `manual_cluster_review` 字段，再进入正式 finalize。
