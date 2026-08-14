# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：JD13～15 extraction acceptance 付费执行（2026-08-14）

### 任务内容

- 经用户明确授权向外部 DeepSeek 发送 JD13～15 真实内容后，按现有正式真实 JD
  extraction acceptance 入口执行付费 `--execute`。
- 参数固定为 `deepseek-v4-flash`、Prompt 0.10、Schema 3.0、每 JD 3 runs、
  `max_attempts=2`；未修改 JD 内容、Prompt、Schema，未 finalize extraction，未启动
  consolidation。

### 验证结果

- Report 与 raw 身份一致：job_ids=13～15，JD set fingerprint 为
  `30877883565ae560608a5026c0dbb42a13e8afd4a3e136931349530ced96fcd1`。
- 9/9 runs 成功，failed runs=0，`passed=true`，`hard_gate_failures=[]`。Requirement counts：
  JD13=31/27/29，JD14=64/60/60，JD15=16/14/14。
- 机器验收产生 9 条稳定性 warning、2 条 diagnostics；均非 hard gate。报告记录的候选
  requirement 总数为 111，需外部 Reviewer 结合 raw 做语义审核并选择 source run。
- Public report 为 `reports/P0-3/jd13-15-acceptance-report.json`，private raw 为
  `data/private/experiments/p0_3/real_jd/jd13-15-acceptance-raw.json`；两者均按规则保持 Git 忽略。
- Acceptance raw 未记录实际 retry 次数；只能确认两阶段成功请求下限为 18 次、授权上限为
  36 次，不对实际计费请求数作无证据推断。
- 正式数据库中 JD13～15 的 extraction 数仍均为 0。
- 全量测试 `365 passed`，`ruff check app scripts tests` 通过。

### 当前状态与下一步

JD13～15 extraction machine acceptance 已完成，无需自动重跑。下一步由外部 Reviewer
审核 9 个 extraction result、选择每个 JD 的 approved run 并给出 result fingerprint；在此之前
不得 finalize extraction，也不得启动 15 JD consolidation。

### 执行提交

- 仅覆盖更新本评审摘要；真实 report/raw 保持 Git 忽略，未修改正式数据库或业务代码。
