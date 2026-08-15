# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：项目、仓库与本地目录更名（2026-08-15）

- 公开项目名由 `JD Skill Insight` 统一更新为 `JD Requirements Insight`，Python 项目元数据
  名称同步更新为 `jd-requirements-insight`。
- GitHub 仓库由 `jd-skill-insight` 更名为 `jd-requirements-insight`，并同步本地 `origin`
  URL；现有 Git 历史与 `v0.1.0-mvp` tag 保持不变。
- 本地 checkout 目录同步由 `jd-skill-insight` 更名为 `jd-requirements-insight`；目录改名后
  已复核 Git 工作区、远端地址和 CLI 入口。
- 仅修改命名、公开说明和仓库地址，未修改 MVP 功能、Prompt、Schema、正式数据、归并结果
  或公开 sample 内容，未调用 LLM。
- 完成旧名称残留检查、锁文件一致性检查、相关 CLI smoke test 与 Ruff 验证。
