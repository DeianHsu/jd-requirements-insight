# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：15 JD consolidation acceptance 付费执行（2026-08-14）

### 任务内容

- 经用户明确授权向外部 DeepSeek 发送 15 JD 的 409 条真实 requirement instances 后，
  按冻结配置执行 consolidation acceptance：3 次 independent runs + 1 次顺序变形，
  `max_attempts=3`。
- 未修改代码、Prompt 4.3 或 Schema 3.0，未 finalize consolidation，未生成市场报告；
  按原任务范围未运行全量 pytest。

### 验证结果

- 输入身份保持 job_ids=1～15、409 instances、fingerprint
  `1696b89d4ea74b6b2c5581630ad325b2a6d252f60937fc6f264856ad3a63e78a`；
  model=deepseek-v4-flash、Prompt=4.3、Schema=3.0。
- 三个 independent runs 均成功，attempts=3/1/3；canonical 数=299/313/298，
  mappings 均为 409，coverage=100%，结构违规/重复映射/未知引用/空 cluster 均为 0。
- Independent result fingerprints 为 `d12ab7f5…b29e` / `387405b7…9748` /
  `46806658…de9`；positive-pair Jaccard 为 34.28% / 64.32% / 36.59%，形成
  3 条稳定性 warning。
- 顺序变形在 3 次尝试后仍失败：模型响应不是合法 JSON，因此 machine acceptance
  记录 1 个 hard-gate failure；本批实际模型请求为 10 次，未超过授权上限 12 次。
- Report `reports/P0-4/15jd-acceptance-report.json` SHA-256 为 `f949380f…b5e2`；
  private raw `data/private/experiments/P0-4/15jd-acceptance-raw.json` SHA-256 为
  `e123b611…ceec`。正式 consolidation 仍为 4 份。

### 当前状态与下一步

15 JD consolidation machine acceptance 尚未完成：顺序变形失败是当前 blocker。
未自动重跑、未选择 source run、未做人工裁决或 finalize；等待外部 Review 决定下一步。

### 执行提交

- 仅覆盖更新本评审摘要；acceptance report/raw 保持 Git 忽略，未修改正式数据库、
  业务代码或其他状态文档。
