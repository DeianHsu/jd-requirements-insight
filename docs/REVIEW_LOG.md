# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。完成开发、代码、文档或
测试修改时覆盖更新；纯讨论、分析和方案会话不同步。历史由 Git 保存，当前项目事实以
`docs/CURRENT_STATE.md` 为准。

## 最近一轮：实现一键自动分析 MVP（2026-08-24）

### 结论

使用者主线已从逐批 acceptance / 人工审核 / finalize 收紧为一条命令：
`analyze-jds <directory> --execute`。它在按输入指纹隔离的私有运行空间中完成 JD 导入、
两段式抽取、自动硬门正式化、单次保守归并、统计和 Markdown 报告；日常使用不再要求
人工裁决。原有多次 acceptance、稳定性分析、人工规则/cluster 审计和文件型 finalize
继续作为开发者改变模型、Prompt、Schema 或规则后的高保证离线评测工具。

### 实现与安全边界

- 抽取与归并分别抽出人工/系统策略共用的正式化写入核心，保留身份字段、结果指纹、
  回读校验、幂等检查和结构门禁；模型生成函数仍不能直接写正式表；
- `auto-v1` 记录 `approval_mode=automatic`、策略版本、批准运行、输入/结果指纹和系统
  批准主体；抽取收齐整批合法结果后才在一个事务中写入；
- 任一 JD 抽取失败时不留下正式抽取；归并失败时不产生正式归并或报告；完成摘要存在且
  数据库/报告齐全时，同一输入直接复用，不再次调用模型；
- 自动硬门只证明 Schema、覆盖、证据存在性、精确 requirement ID、唯一 mapping、结构
  与 provenance 合法，不宣称自动证明 evidence 支持性或 cluster 语义完全正确；报告仍是
  MVP 分析结果，不等同于人工审计结论；
- 付费调用仍必须显式 `--execute`，测试全部使用 fake 客户端和临时数据库，没有调用
  外部付费服务。

### 文档与规则

- `AGENTS`、README、CURRENT_STATE、ARCHITECTURE、VALIDATION、GLOSSARY、app/scripts
  导航均已把一键命令设为使用者主线，把人工审核移到开发者评测周期；
- Review Log 同步规则改为：开发、代码、文档或测试修改后更新；纯讨论、分析和方案会话
  不更新。

### 提交与验证

- `46315c0 refactor(finalize): 抽取共用正式化写入核心`；
- `4a939f6 feat(pipeline): 增加一键自动分析主线`；
- 本次文档、合同测试和 Review Log 收口提交；
- `.venv\\Scripts\\python.exe -m pytest --basetemp .pytest-tmp`：343 passed；
- `.venv\\Scripts\\python.exe -m ruff check app scripts tests`：通过；
- 公开 sample 重新生成后无差异；27 个 Markdown 文件的本地链接检查通过；
- `uv run` 仍因本机全局 uv cache 的 `.git` ACL 拒绝访问而无法启动，已使用项目 `.venv`
  完成等价验证；该环境问题不影响仓库测试结论。
