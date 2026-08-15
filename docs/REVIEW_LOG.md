# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：实施 annotation 文档收缩（2026-08-15）

### 完成内容

- 将 `REQUIREMENTS.md` 与 `RESPONSIBILITIES.md` 合并为
  `docs/EXTRACTION_RULES.md`；RESP-01/02 的业务规则完整保留，重复的职责验证边界删除；
- 将 `annotation/VALIDATION.md` 迁移为 `docs/VALIDATION.md`，职责保持为证据、覆盖、
  变形、稳定性以及抽取/归并 acceptance 合同；
- VALIDATION 的旧数据处理段压缩为当前合同边界：仅接受 v0.10 + Schema V3 与现行
  数据库结构，其他版本或结构明确拒绝，不迁移、不兼容；
- 删除旧 annotation 三文件和本地空目录，文档合同由三份收缩为两份；
- 同步 AGENTS、README、CURRENT_STATE、应用模块、验收脚本、测试 docstring 和
  Pipeline 文档路径检查；Prompt 只更新规则文档定位与过期的 P0-1 描述。

### 核实结论

- RESP / REQ / GROUP / FIELD / EVID / COVER 规则 ID 均保留；
- category、importance、proficiency、逻辑组、年限、证据和覆盖语义未改变；
- Schema、Prompt 业务规则、正式数据库、验收门禁和 CLI 行为未改变；
- 当前仓库不再存在 `docs/annotation/`，也没有仍指向旧文件的有效链接或运行时注释。

### 验证

- annotation 相关专项测试：**94 passed**；
- 全量测试：**337 passed**；
- Ruff：通过；
- 11 份当前 Markdown 的本地链接：无失效目标；
- 公开 sample 重新生成后无差异；
- `uv run pytest` 因当前 Windows uv cache 权限问题未启动，已使用同一项目虚拟环境完成
  等价全量测试；
- 未调用付费模型，未写正式数据库，未修改私有 artifact；
- 本文件随最终总结提交。
