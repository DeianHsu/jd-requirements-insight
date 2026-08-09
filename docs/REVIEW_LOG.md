# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：项目现状与生产主线 L3 审计（2026-08-09）

### 核实结论

- 当前正式主线已走通到 8 JD 批次：8 份 JD、8 份正式抽取、3 个正式归并
  批次；当前批次 #3 为 211 mappings / 174 canonical，离线验证 coverage
  100%、结构违规 0、reportable=True，正式报告存在且含原文 evidence。
- JD 4～8 的验收 report/raw 文件指纹和正式抽取结果指纹均可复算且与数据库
  一致；JD 1～3 保持 `unverified`，仅由已提交的 P0-7 结构化历史豁免覆盖，
  报告中保留 provenance 风险提示。
- 候选抽取/归并不会写正式表；正式 E2E 调用验收脚本、人工审核模拟、
  finalize 和 generate-report，没有手工拼接模型中间结果。
- 当前基线未发现阻塞 8 JD 批次使用的问题。扩样前存在一个下一阶段门禁：
  `generate-report` 会把任意未 `fully_bound` 的来源直接描述为受 JD 1～3
  历史豁免覆盖，但没有读取和核对豁免文件的 job_ids/allowed_use；当前批次
  恰好匹配，不影响现有报告，新增 JD 前应修复并补失败路径测试。

### 验证结果

- `audit-extraction-sources`：JD 1～3 = `unverified`，JD 4～8 =
  `fully_bound`。
- `audit-consolidation --consolidation-id 3`：reportable=True；211 mappings，
  174 canonical，来源 run-2。
- `validate-consolidation --consolidation-id 3`：coverage 100%，结构违规 0。
- 全量测试：352 passed；正式主线 E2E：4 passed。
- `ruff check app scripts tests`：通过。
- 未调用付费模型，未修改正式数据库或私有产物。

### 当前状态与下一步

项目已完成 MVP 的 8 JD 可演示闭环，下一业务阶段是 8 → 12 → 15 JD 扩样。
最小顺序：先收紧历史豁免范围门禁，再由用户提供 4 份新 JD，逐批执行现有
正式主线并记录抽取成本、归并稳定性和人工裁决量；无真实阻塞后扩到 15 JD。

### 执行提交

- 仅覆盖更新本评审日志；未修改业务代码和状态文档。
