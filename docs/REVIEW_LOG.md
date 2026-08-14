# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：JD13～15 extraction acceptance 付费前预检（2026-08-14）

### 任务内容

- 按现有正式真实 JD extraction acceptance 入口，对 JD13、JD14、JD15 执行纯离线
  `--dry-run`；未传入 `--execute`，未调用模型，未 finalize extraction，未启动
  consolidation。
- 固定参数为 `deepseek-v4-flash`、Prompt 0.10、Schema 3.0、每 JD 3 runs、
  `max_attempts=2`；未修改 JD 内容、Prompt、Schema 或业务代码。

### 验证结果

- 正式项目数据库中 JD13～15 均存在且均无正式 extraction；三份文件均可由当前
  `JobDocument` 输入合同解析，文件路径、job_id、title、company、city 与数据库身份一致，
  原文 fingerprint 也与数据库 `source_hash` 一致。
- JD13、JD14、JD15 的输入 fingerprint 依次为
  `08984a31c187f9a58e7ac38aa9e7404cfa7922853669f2cdd7ca1673fa14ee98`、
  `94511dc32952389bae9e7d07f06175d473b8665f4e37822372e3b4bc7df4f5ad`、
  `83de867cb73270ad017e492ebcab18b744cf1c947fe2264de06b3da0209f7597`；
  JD set fingerprint 为
  `30877883565ae560608a5026c0dbb42a13e8afd4a3e136931349530ced96fcd1`。
- dry-run 成功识别 3 个 JD、9 个独立 runs；两阶段抽取预计 18 次模型请求，按每阶段
  最多 2 attempts 计算上限为 36 次。模型配置已就绪且当前模型名匹配。
- 目标 public report 为 `reports/P0-3/jd13-15-acceptance-report.json`，private raw 为
  `data/private/experiments/p0_3/real_jd/jd13-15-acceptance-raw.json`；dry-run 后两者均未生成，
  不存在路径冲突。
- 全量测试 `365 passed`，`ruff check app scripts tests` 通过。

### 当前状态与下一步

付费前预检通过，无 blocker。下一步仅在用户再次明确授权后，使用同一组参数将 dry-run
命令的 `--dry-run` 替换为 `--execute`，启动 JD13～15 的 9-run extraction acceptance；
本轮已停止，未执行该付费命令。

### 执行提交

- 仅覆盖更新本评审摘要；未修改正式数据库、JD、Prompt、Schema 或业务代码。
