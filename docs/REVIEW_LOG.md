# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：人工审核职责说明（2026-08-24）

### 核实结论

正常的新数据正式化只包含两次必要的人工语义判断：抽取审核回答“每份 JD 抽取得是否
正确”，归并审核回答“跨 JD 要求是否应该归为同一 canonical”。两个 finalize 都是读取
审核结果并执行身份、指纹、覆盖和事务门禁的自动步骤，不是额外人工审核；历史 waiver
是一次性风险接受，也不是常规数据审核。

### 各审核点

- **抽取 acceptance / 人工规则审核**：检查 responsibility 是否误抽为要求、要求是否
  原子化、是否凭常识补出不存在的技能、evidence 是否真正支持名称、importance、
  proficiency 和年限，以及 any_of/standalone 是否符合原文；最后批准某个具体 run。
- **归并 acceptance / cluster 审核**：检查所有多成员 cluster 中的实例是否语义等价，
  处理稳定性和顺序变形 warning，作出 must-link、cannot-link 与 canonical 名称裁决，
  并批准作为正式结果基础的具体 run。
- **历史 provenance waiver**：只判断来源绑定缺口是否可在精确范围内接受，记录批准人、
  风险、证据、允许的下游和新增数据禁用边界；记录仍保持 `unverified`，不伪装成
  `fully_bound`。

### 自动部分与边界

- Schema、证据存在性、分句覆盖、精确 requirement ID coverage、重复/未知 mapping、
  report/raw 身份、文件和结果指纹、幂等与回滚均由代码自动检查；
- `finalize-extraction` 和 `finalize-consolidation` 不重新判断语义，只验证审核绑定后写入；
- `generate-report` 没有第三轮人工定稿，通过正式数据与 provenance 门禁后确定性生成；
- 当前 fresh JD 不允许继承历史 waiver，因此常规流程没有额外的 waiver 审批；
- 本轮只完成职责核实和说明，未修改代码、正式数据库或私有 artifact；本文件随总结提交。
