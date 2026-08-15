# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：annotation 文档剪枝分析（2026-08-15）

### 分析结论

当前三份 annotation 文档可以收缩为两份，但不建议全部合成一份：

- `REQUIREMENTS.md`（约 9 KB）是“抽取什么、字段如何解释”的语义合同，应保留主体；
- `RESPONSIBILITIES.md`（约 1 KB）只有 RESP-01/02 和一段验证边界，其内容已分别被
  REQUIREMENTS 的要求边界、VALIDATION 的 COVER-04、ARCHITECTURE 和 Prompt 覆盖，
  没有继续独立存在的职责；
- `VALIDATION.md`（约 5 KB）是“如何证明抽取/归并可接受”的验收合同，并被应用代码、
  两个验收脚本和多组测试直接引用；它与标注语义的读者和修改触发条件不同，应独立保留。

### 推荐实施方案

1. 将 RESP-01/02 的唯一业务语义并入要求规则文档，删除重复的“验证边界”说明；
2. 将合并后的 `REQUIREMENTS.md` 移到 `docs/EXTRACTION_RULES.md`，职责明确为抽取语义、
   原子化、逻辑组和字段判定；
3. 将 `annotation/VALIDATION.md` 移到 `docs/VALIDATION.md`，保留证据、覆盖、变形、
   稳定性和归并验收合同；
4. 删除空的 `docs/annotation/` 目录，更新 README、CURRENT_STATE、Prompt 注释、脚本、
   测试和文档内部链接；
5. 从 VALIDATION 删除仓库级“非当前数据处理”重复条款（以 AGENTS 为事实源），并把
   P0-3/P0-4 章节名收成“抽取 acceptance / 归并 acceptance”；真实脚本路径不改名。

### 保留边界

- 不删 category、importance、proficiency、逻辑组和年限的业务解释；代码枚举不能替代
  这些人工判定规则；
- 不合并 VALIDATION 与 EXTRACTION_RULES，避免形成同时服务标注者、实现者和验收者的
  大型混合文档；
- 不修改规则 ID、Prompt 语义、Schema、代码行为或验收门禁；本轮只完成分析，尚未实施。

### 核实

- 当前工作树在分析前干净；
- 已读取三份文档全文并核对代码、脚本、测试和其他文档引用；
- 本轮未修改 annotation 文档、代码、测试、正式数据库或私有 artifact；
- 本文件随分析摘要提交。
