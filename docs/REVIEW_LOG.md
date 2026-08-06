# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：backfill raw provenance 边界核实与修复（2026-08-07）

### 评审要求（要点）

1. 核实 raw 批准运行的记录 `result_fingerprint` 是否真正验证与 `result` 内容一致；
2. 核实 raw 整轮身份与选中运行标识是否与 report、批次完整绑定；
3. 区分"结果可由来源运行 + decisions 确定性重建"与"审核人/审核时间具有同等强度的机器证明"。

### 核实结论（独立读码，全部成立）

- **记录指纹被直接信任**：`_run_fingerprint` 有记录值时直接返回，不验证与 `result` 内容一致（此前仅被重放间接兜底）；
- **raw 整轮身份完全未校验**：backfill 只校验 report/final 身份，raw 顶层 selected_job_ids/input_fingerprint 等从不检查——传错批次的 raw 不会被发现（比评审描述的更明显）；
- **证明强度两级**：结果重建是机器强证明；审核人/时间是验收报告文件声明（旧批次无报告指纹锚点，无法机器复核）。

### 执行（1 个提交）

- `d697943` fix(production): backfill 补齐 raw provenance 边界——
  - 批准运行记录指纹（存在时）显式校验 == `result` 内容指纹；`run_identifier`（存在时）== `run-N`；
  - raw 顶层整轮身份：存在字段必须与批次一致（防错文件）；旧格式 raw（`acceptance-final.json` 缺 prompt_version/schema_version，是批次 #1 唯一可用 raw）缺失字段由验收报告身份兜底；
  - docstring 新增证明强度两级说明（结果可机器证明 / 审核元数据为声明记录，人工审核行为固有边界）；
  - +3 测试（记录指纹与内容不一致拒绝、raw 身份不一致拒绝、运行标识不一致拒绝）。

### 验证结果

- 全量 **352 测试通过**（+3）、ruff 全过；
- 真实库副本验证：批次 #1/#2 全部校验通过（含 raw 身份绑定与批准运行内容指纹）；
- 已 push（`cc89308..d697943`）。

### 当前状态

- backfill 证据链覆盖：final-result 内容指纹、final 批次身份、raw 整轮身份、批准运行记录指纹/内容一致性、运行标识、真正重放、类型/格式干净拒绝；
- 证明强度表述已在 docstring 区分（结果可机器证明；审核元数据为历史声明）；
- 非阻塞待办不变：`reviewed_unbound` 命名细分；归并稳定性 5→6→7→8 测量；**JD 1～3 豁免/重验悬置决策**（报告持续标注 provenance）。
