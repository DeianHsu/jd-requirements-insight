# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：私有批次信息与公开文档分离（2026-08-15）

### 完成内容

- 删除职责已经结束的 `docs/PROJECT_PLAN.md`；v0.1 冻结状态和维护边界由
  `CURRENT_STATE` 统一承接，AGENTS 阅读顺序、事实优先级和文档路径测试同步更新；
- README 删除私人数据集的精确规模、统计结果、市场结论和具体例外，只保留作品说明、
  可公开验证的方法、合成 sample 与通用安全边界；
- CURRENT_STATE 收缩为当前软件基线、安全门和公开/私有边界，不再作为私人批次报告；
- 针对具体真实 JD 的 provenance waiver 从受版本控制路径迁入 `data/private/`，报告
  门禁仍严格校验批准信息、适用范围、证据、风险和新增记录边界；
- 新建被 Git 忽略的私有 release manifest，以路径和 SHA-256 关联当前正式数据库、
  acceptance、人工裁决、最终结果与报告；未删除其他本地历史 artifact。

### 验收结论

- 当前公开工作树不再保存私人批次的精确指标、市场发现或具体 waiver 内容；
- 正式链仍为 LLM 多次运行 → acceptance / validation → human review → finalize →
  formal DB；单次预检 candidate 仍不进入 finalize；
- 私有 waiver 缺失、非法或未覆盖新增来源时，报告入口继续拒绝生成；合法的既有范围会
  放行并在报告中保留 provenance 风险提示；
- 当前基线无 blocker；未授权下一阶段仍不存在；Git 历史中的旧版本未改写。

### 验证

- 全量测试：**337 passed**；Ruff：通过；
- 正式数据库只读 consolidation 审计与结构验证：通过；
- 使用迁移后的私有 waiver 从正式入口生成临时报告：通过，临时文件已删除；
- 12 个当前受版本控制 Markdown 的本地链接：无失效目标；
- 公开 sample 重新生成后无差异；
- 未调用付费模型，未写正式数据库，未删除旧 Golden 或其他历史 artifact；
- `uv run` 受当前 Windows uv cache/trampoline 权限问题影响，测试与命令使用同一项目
  虚拟环境中的 Python/Ruff 完成。

### 提交

- `5f2a807 refactor(provenance): 私有化历史来源豁免`
- `9d46b9e docs(public): 收缩公开文档的私人批次信息`
- 本文件随最终总结提交。
