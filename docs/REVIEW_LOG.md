# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：第五轮评审收口（2026-08-07）

### 评审要求（要点）

1. **P0-1**：`finalize-extraction` 仍接受旧的不完整身份合同（job_ids/jd_set_fingerprint/runs/max_attempts "存在才比较"）——应强制 8 字段完整合同，历史产物只走 `verify_extraction_source`；
2. **P0-2**：backfill 只证明"字段补上"未证明裁决链——`--raw-output` 应必填、须验证批准运行指纹与历史 final-result 证据链、已有字段冲突应拒绝不覆盖、checked_at 用真实时间、补回归测试；
3. P1：`reviewed_unbound` 命名过乐观（非阻塞，未处理）。

### 核实结论

- P0-1、P0-2 指控**全部属实**（已逐条核对代码）；另发现评审未提的连带影响：现有正式批次 #1/#2 缺 5 个定稿字段，直接加强门禁会阻断现有报告，需配套离线补齐；
- 文件对应确认：批次 #2 → `acceptance-5jd-raw.json` + `final-consolidation-5jd-v2.json`；批次 #1 → `acceptance-final.json`（旧格式 runs 无 result_fingerprint，需重算）+ `final-consolidation.json`。

### 执行（3 个提交）

- `59d9611` fix(production): 关闭旧格式 finalize 通道——8 字段完整身份合同硬要求（缺失即拒、report/raw 全字段一致、report jobs 集合 == raw job_ids），夹具升级新合同，+2 测试；
- `1cadd97` feat(production): backfill 重写为重放式安全门——`--raw-output`/`--final-result` 必填，完整证据链校验（锚点已存在 → 批准运行指纹（缺失重算）== 验收报告批准指纹 == 历史最终结果 source_result_fingerprint → decisions 指纹链 → 最终结果指纹 == 当前持久化结果），已有字段冲突拒绝不覆盖，checked_at 用实际时间，+7 临时库回归测试；
- `db72512` style(tests): 移除 backfill 测试未使用 import（ruff F401）。

### 验证结果

- 全量 **344 测试通过**（+9）、ruff 全过；
- 按评审要求先在**数据库副本**上重放验证批次 #1/#2（证据链完全吻合），再操作正式库；audit `reportable=True` 保持；
- 已 push（`ba966b4..db72512`）。

### 当前状态

- P0-7 两个历史兼容口已封死：finalize 只接受完整新合同；backfill 可证明完整裁决链；
- 非阻塞待办：`reviewed_unbound` 状态命名细分；归并稳定性 5→6→7→8 测量；**JD 1～3 豁免/重验悬置决策**（报告持续标注 provenance）；
- 「评审日志」规则（AGENTS.md）已按"覆盖更新、只保留最新一轮"执行。
