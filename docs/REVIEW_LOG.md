# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准，
v0.1 关闭决策以 `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：实施剪枝后的全仓文档收缩（2026-08-15）

### 完成内容

已按批准方案完成文档职责重构，并吸收最后两项信息源分工：

- `AGENTS.md` 切换到“v0.1 已完成并冻结”的维护生命周期；正式主线补齐
  acceptance → human review → finalize，单次 candidate 明确为可选预检；
- 新会话阅读顺序调整为 AGENTS → README → CURRENT_STATE → ARCHITECTURE →
  按需读取 PROJECT_PLAN/GLOSSARY/annotation；
- CURRENT_STATE 成为当前事实源，PROJECT_PLAN 只负责 v0.1 关闭决策；
- PROJECT_PLAN 只保留 JD1～3 provenance 和归并稳定性两个 accepted exceptions，
  `reviewed_unbound` 仅作为 CURRENT_STATE 的非阻塞卫生项；
- GLOSSARY 删除“正式 JD 样本范围 15～20”状态型术语，抽取器版本改为验收、定稿、
  下游选择和 provenance 身份，流水线不变量不再依赖 P0 阶段编号。

### 正式链与文档命令

- ARCHITECTURE 将正式数据链与可选单次 candidate 画成两条路径；
- README 同步正式链，把 candidate 放入独立的“可选单次预检”小节；
- README 保留 JD1～3 的简洁 provenance 风险提示并链接 CURRENT_STATE；
- 修正 P0-4 验收命令：使用必填 `--database-url`、`--raw-output` 和
  `--execute`，不再记录不存在的 `--use-project-database --all`；
- 文档合同测试同步验证真实参数，并检查 CURRENT_STATE 明确 candidate 不作为 finalize
  输入；
- app/scripts README 更新为剪枝后实际模块和 5 个实验脚本的导航；
- VALIDATION 标题调整为同时覆盖抽取与归并，规范正文保持不变。

### 公开 sample

`scripts.make_sample_report` 的合成 requirement 不再统一写成 `other/basic`：

- 编程语言、经验、RAG、软技能、学历使用对应 category；
- “掌握常用编程语言”使用 `advanced`；
- 经验、软技能和学历在没有明确程度时使用 `unknown`；
- “本科及以上学历”作为普通任职要求使用 `must`；
- `examples/market-report-sample.md` 已由生成器重新生成，没有手工修改生成产物。

### 验证

- 全量测试：**336 passed**；
- Ruff：通过；
- 文档合同专项测试：通过；
- 市场报告专项测试：**33 passed**；
- 13 个受版本控制 Markdown 的本地链接：无失效目标；
- sample 连续两次生成内容一致，SHA-256：
  `844946667FB2FB25D89E9C4F6DA93B91B879DD26B6ECF744E2A98100FEF64A6B`；
- `uv run pytest` 因当前 Windows 环境的 uv trampoline 路径错误未启动，已使用同一
  项目虚拟环境完成等价全量执行；
- 未调用付费模型，未写项目正式数据库，未修改私有或未跟踪历史 artifact。

### 提交

- `25ef4b1 docs(lifecycle): 切换到 v0.1 冻结维护`
- `b73270e docs(pipeline): 分离正式链与单次预检`
- `91b6aed fix(sample): 对齐公开样例字段语义`
- 本文件与 AGENTS 的最终排版修正随总结提交。
