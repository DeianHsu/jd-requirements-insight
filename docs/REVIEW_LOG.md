# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：annotation 剪枝方案最终修订（2026-08-15）

### 最终结论

annotation 文档仍按已批准方向从三份收缩为两份：

- REQUIREMENTS 与 RESPONSIBILITIES 合并为 `docs/EXTRACTION_RULES.md`；
- VALIDATION 独立迁移为 `docs/VALIDATION.md`；
- 删除失去职责的重复说明和空的 `docs/annotation/` 目录；
- 不改变规则 ID、Prompt 语义、Schema、代码行为或验收门禁。

### 本轮调整

VALIDATION 的“当前版本适用范围”不完全删除，而是压缩为一条当前合同边界：

> 仅接受 v0.10 + Schema V3 与现行数据库结构；其他版本或结构明确拒绝，不迁移、不兼容。

该条属于验证合同自身必须声明的适用范围。备份原始 JD、删除旧派生数据库并重新生成等
操作指引不在 VALIDATION 重复，继续以 AGENTS 的“验证与实验”规则为准。

### 状态

- 调整后的方案通过，可以作为后续实施依据；
- 本轮只更新方案记录，尚未移动、合并或删除 annotation 文档；
- 未修改代码、测试、正式数据库或私有 artifact；
- 本文件随方案修订提交。
