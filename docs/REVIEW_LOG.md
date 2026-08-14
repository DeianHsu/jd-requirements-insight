# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：修复 15 JD 增量审核候选召回（2026-08-14）

### 任务内容

- 独立复现上一版审核清单的候选漏召回：虽然 IDs 1～300 已冻结为正式 12 JD partition，
  候选发现仍只扫描 run1，错误地把 source candidate 与候选观察范围合并。
- 最小修复为：run1 继续作为 source candidate；候选召回改用 3 个成功 independent runs
  的原子 `new→frozen canonical` / `new↔new` 关系并集，并保留每条边的 supporting runs。
- 展示层仅按候选图连通分量分组，不自动推导传递 must-link；纯 old↔old 关系仍为 0。
  未调用模型、未重跑 consolidation、未修改业务代码/Prompt/Schema/数据库，未 finalize、
  未运行 pytest。

### 验证结果

- 正式 consolidation #4 与 12 JD final artifact 仍完全一致：241 canonical / 300
  mappings，IDs 1～300 partition 未变化。
- 修复前为 20 个审核组、26 个有关联新增 ID、83 个 standalone 声明；run1-only 实际
  漏掉 8 条 `new→frozen` 和 7 条 `new↔new` 原子边，影响 12 个新增 ID。
- 修复后为 23 个展示审核组、66 条原子候选关系（52 条 `new→frozen`、14 条
  `new↔new`）、36 个有关联新增 ID；三个成功 run 均未观察到关系的新增 ID 降为 73，
  其状态改为“无已观察关系、新 canonical fallback”，不再宣称确定 standalone。
- 已知漏项 347、374、409、325/326/327、333/334、338/339/340 全部恢复；额外发现并
  恢复同类漏项 399。逐 run 反查全部观察边，修复后 missing=0。
- Order transformation 仍为 `successful=false`，正式 hard gate 数量仍为 1。

### 门禁与当前 blocker

- 现有正式流程仍没有 consolidation order-transformation hard-gate 的 waiver/exception；
  当前 1 条 hard gate 继续阻止 finalize。本轮仅修正审核材料，不处理该门禁。

### 审核材料

- `data/private/experiments/P0-4/15jd-incremental-review-checklist.md`
- `data/private/experiments/P0-4/15jd-incremental-review-checklist.json`

等待外部 Reviewer 对 23 个展示审核组中的 66 条明确原子候选关系做语义裁决；当前不具备
正式 finalize 条件。
