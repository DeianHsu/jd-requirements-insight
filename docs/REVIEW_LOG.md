# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：JD 9～12 正式抽取验收（2026-08-09）

### 任务内容

- 经用户明确授权，将 JD 9～12 真实内容发送给外部 DeepSeek 模型，执行
  v0.10 + Schema V3 正式抽取验收。
- 配置为 deepseek-v4-flash、Prompt 0.10、Schema 3.0、每 JD 3 runs、
  每阶段 max_attempts=2；未执行正式定稿、归并或报告生成。

### 验证结果

- 12/12 个 run 成功，hard gate=0，验收通过；要求数：JD 9 为 36/36/32，
  JD 10 为 8/8/8，JD 11 为 23/24/24，JD 12 为 25/21/19。
- 报告包含 6 条非阻塞稳定性 warning、2 条诊断；JD 9/11/12 存在不同
  程度的未匹配项，JD 12 漂移最大，需人工审核后才能选择批准 run。
- 脱敏报告位于 `reports/P0-3/jd9-12-acceptance-report.json`，私有原始结果
  位于 `data/private/experiments/p0_3/real_jd/jd9-12-acceptance-raw.json`；
  两者均被 Git 忽略。
- 正式数据库未被验收流程写入：JD 9～12 extraction 记录均为 0。
- 产物未保存逐阶段尝试次数，只能确认调用数处于授权的 24～48 次范围，
  无法精确复算实际请求数。
- 全量测试使用新 basetemp 复跑为 357 passed；
  `ruff check app scripts tests` 通过。首次复用旧 `.pytest-tmp` 时因 Windows
  目录锁在 fixture 初始化阶段失败，不属于代码回归，未删除该旧目录。

### 当前状态与下一步

JD 9～12 已完成机器验收但尚未定稿。下一步是人工审核语义与证据、选择
每份 JD 的批准 run，再离线 finalize extraction；之后才能启动 12 JD 全量
归并验收。JD 13～15 保持未付费抽取状态。

### 执行提交

- 本轮仅同步当前状态与评审摘要；正式数据库、原始 JD 和模型产物均不提交。
