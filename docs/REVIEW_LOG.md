# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：12 JD 离线稳定性分析（2026-08-10）

### 任务内容

- 使用现有 `scripts/experiments/p0_4/analyze_stability.py` 对 12 JD
  acceptance raw 执行纯离线稳定性分析，生成 public 脱敏报告与 private
  人工审核分析；未调用模型或创建新归并运行。
- 分析前及脚本内部均核对当前正式数据库：job_ids=1～12、300 instances、
  raw/数据库 input fingerprint 完全一致。

### 验证结果

- 4 个观察全部纳入：稳定对 63、不稳定对 230、不稳定跨 JD 对 151；
  27 个稳定跨 JD 核心簇中有 14 个 market-impact、13 个 edge-only。
- 最大 distinct-job-count 漂移为沟通能力 3～8、问题分析 2～7、团队协作
  4～8、问题解决 3～7、大模型应用开发 2～4，应优先人工审核。
- 8 JD 历史 11 条裁决中有 6 条在至少一个观察被破坏；未修改历史决定。
- public：`reports/P0-4/12jd-stability-report.json`；private：
  `data/private/experiments/P0-4/12jd-stability-analysis.json`。正式数据库仍
  只有 consolidation #1～#3。
- 全量测试 357 passed；`ruff check app scripts tests` 通过。

### 当前状态与下一步

12 JD 离线 stability analysis 已完成，人工审核材料已准备好；不存在阻止
进入外部语义 Review 的身份或产物问题。未选择 source run、未生成裁决、
未 finalize。JD 13～15 保持未付费抽取状态。

### 执行提交

- 本轮仅提交当前状态与评审摘要；正式数据库、真实验收报告、原始 JD 和
  模型产物均保持 Git 忽略。
