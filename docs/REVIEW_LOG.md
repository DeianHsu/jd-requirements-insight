# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：backfill 重放核实与修复（2026-08-07）

### 评审要求（要点）

1. 核实 `backfill_consolidation_metadata.py` 是否验证 final-result 的 `result` 内容与其声明指纹一致、是否完整核对批次身份；
2. 核实"重放式校验"表述是否强于实际实现；
3. 检查 `approved_run_index` 非法类型与 `reviewed_at` 格式错误是否干净拒绝且不写库。

### 核实结论（独立读码，三项全部成立）

- **final-result 内容指纹未校验**：代码只比较"声明 result_fingerprint vs 当前库复算指纹"，从未解析 `final["result"]` 内容——篡改 result 内容但同步声明指纹即可通过；
- **final-result 批次身份未核对**：docstring 声称校验 6 个身份字段，实现只查 4 个链字段非空 + 3 个比对，身份字段未实现（声明强于实现）；
- **"重放式"表述过强**：原实现是历史证据链一致性检查（校验声明字段自洽），未从批准运行 + decisions 重新生成结果；
- **approved_run_index 非 int（如字符串 "0"）→ `0 <= "0"` TypeError 崩溃**（非干净拒绝）；**reviewed_at 非法格式未校验即写入**。

### 执行（1 个提交）

- `cc89308` fix(production): backfill 实现真正重放——
  - 解析 `final["result"]` 校验内容指纹 == 声明 result_fingerprint；
  - 校验 final-result 6 个批次身份字段与批次一致；
  - 从 raw 批准运行结果 + 审核决定调用 `_apply_decisions`（与 apply_review_decisions 相同逻辑）**真正重放**，重放结果指纹必须 == 当前持久化结果；
  - `approved_run_index` 类型/范围校验、`reviewed_at` fromisoformat 校验，非法即干净拒绝且不写库；
  - +5 测试（内容指纹不符/身份不符/索引字符串/日期非法/重放不一致——后者验证命中"重放成功但结果不同"分支）。

### 验证结果

- 全量 **349 测试通过**（+5）、ruff 全过；
- 真实库副本重放：批次 #1/#2 真正重放结果与当前持久化结果**完全一致**——"当前结果确由批准运行 + 审核决定确定性产生"从声明变为可证明；正式库无需修改；
- 已 push（`db72512..cc89308`）。

### 当前状态

- "重放式安全门"表述现在与实现一致（真正重放 + 内容指纹 + 批次身份 + 类型干净拒绝）；
- 非阻塞待办不变：`reviewed_unbound` 命名细分；归并稳定性 5→6→7→8 测量；**JD 1～3 豁免/重验悬置决策**（报告持续标注 provenance）。
