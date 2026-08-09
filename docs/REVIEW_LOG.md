# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：12 JD 全量归并机器验收（2026-08-09）

### 任务内容

- 经用户再次授权，将 JD 1～12 的 300 条正式 requirement instances 发送给
  外部 DeepSeek 模型，执行 Prompt 4.3 / Schema 3.0 全量归并验收。
- 执行 3 次独立运行与 1 次顺序变形，每次最多 3 attempts；未执行人工
  裁决、正式归并定稿、报告生成或 JD 13～15 抽取。

### 验证结果

- 四个任务全部成功，实际 attempts 为 1/2/1/2，共 6 次模型请求；
  hard gate=0，所有结果 coverage=100%、300 mappings、结构违规=0。
- 独立运行 canonical=183/211/209；Jaccard 为 51.41%/52.84%/85.12%；
  顺序变形 canonical=238、与 run0 Jaccard=32.30%，共产生 3 条稳定性
  warning，需要离线分析和人工裁决。
- 报告 `reports/P0-4/12jd-acceptance-report.json`，私有 raw
  `data/private/experiments/P0-4/12jd-acceptance-raw.json`；文件 SHA-256
  为 `72ce842d…` / `6fd66310…`。
- 正式数据库未写入新归并批次，仍只有批次 #1～#3。
- 全量测试 357 passed；`ruff check app scripts tests` 通过。

### 当前状态与下一步

12 JD 归并机器合同已通过，但稳定性不足；下一步是离线稳定性分析和必要
人工裁决，之后才能选择批准运行并正式定稿。JD 13～15 保持未付费抽取状态。

### 执行提交

- 本轮仅提交当前状态与评审摘要；正式数据库、真实验收报告、原始 JD 和
  模型产物均保持 Git 忽略。
