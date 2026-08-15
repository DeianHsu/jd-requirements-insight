# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：匿名工程验收信息回调（2026-08-15）

### 完成内容

- README 恢复一组不包含 JD 正文和岗位市场结论的匿名工程验收摘要：真实 JD 数、
  requirement instances、canonical requirements、coverage、结构违规和人工定稿状态；
- CURRENT_STATE 作为事实源同步记录匿名验收事实，并恢复最低 positive-pair Jaccard
  观察值，明确它是实际稳定性 limitation 和诊断指标，不是结构 hard gate；
- 真实 JD、模型原始响应、人工裁决内容、逐记录来源状态、具体 waiver、artifact 身份
  和岗位市场结论继续保持私有；
- README 不再使用任意固定的 consolidation ID；PowerShell 示例通过 `Read-Host`
  获取实际批次 ID，避免不可执行的 `<id>` 占位符和误导性的固定数字；
- 文档合同测试增加通用批次变量检查，并拒绝重新出现 `--consolidation-id 1`。

### 结论与边界

- 本次回调恢复的是“系统确实在真实数据上跑通过”的匿名成绩牌，不是私人市场报告；
- 不需要也未执行 Git 历史重写；具体 waiver 已从当前公开工作树移除，旧历史按现行仓库
  规则保留；
- 未改变 Schema、正式链、统计口径、数据库内容、报告门禁或 annotation 剪枝方案。

### 验证

- 全量测试：**337 passed**；
- Ruff：通过；
- 公开 sample 重新生成后无差异；
- `uv run pytest` 因当前 Windows uv cache 权限问题未启动，已使用同一项目虚拟环境完成
  等价全量测试；
- 未调用付费模型，未写正式数据库，未修改私有 artifact；
- 本文件随最终总结提交。
