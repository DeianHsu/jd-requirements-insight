# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：15 JD frozen-base apply 能力（2026-08-14）

### 修改内容

- 根因确认：15 JD run1 的 IDs 1～300 投影为 229 clusters，无法通过重放旧 decisions
  安全重建已关闭的 12 JD final partition（241 canonical / 300 mappings）。
- 为现有 `apply_review_decisions.py` 增加最小的可选 `--frozen-base` 输入：直接继承
  较小范围的完整 final result，其余 requirement 初始化为 singleton，只应用本轮
  增量 decisions；普通非 frozen 流程保持原行为。
- review-decisions 必须显式绑定 frozen base 的 input/result/review-decisions
  fingerprints、job/requirement IDs 与 canonical/mapping 数量；同时核对当前数据库
  子范围 fingerprint、完整 final metadata、结果内容指纹和现有结构合同。
- 冻结模式拒绝 pure old↔old decision、一次引用多个 frozen IDs、通过共享新增成员
  间接合并两个 frozen canonicals，以及只重命名 frozen canonical；应用后逐 canonical
  验证旧成员、owner、ID 与名称完全不变。最终 canonical name 唯一性未放松。

### 验证结果

- 定向测试：`tests/test_p0_4_frozen_base.py` + `tests/test_p0_4_finalize.py`，
  **38 passed**；覆盖正常增量应用、fingerprint/ID/数量/不完整 final 拒绝、直接和
  间接 pure old 合并拒绝、新 requirement 合并至 frozen canonical、保持独立以及
  既有非 frozen 行为回归。
- Ruff：`apply_review_decisions.py` 与新增测试均通过。
- 真实只读验证：正式数据库 15 JD input fingerprint 与 acceptance raw 一致；12 JD
  子范围 fingerprint 与 frozen artifact 一致；冻结产物结果指纹
  `47591259e0a8decb9288094803136df7f75e6c418408b9dbe8712804975e052f`，精确
  覆盖 IDs 1～300、241 canonical / 300 mappings；加入 109 个新增 singleton 后为
  350 canonical / 409 mappings，冻结 partition 校验通过。

### 当前状态

该能力可以安全承载 15 JD 增量 review-decisions 应用；本轮未创建或应用正式 15 JD
decisions、未生成 candidate、未 finalize、未调用模型。order transformation hard gate=1
仍是后续独立 blocker，未处理或绕过。等待外部 Review 后进入 15 JD decisions 应用。
