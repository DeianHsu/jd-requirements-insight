# P0功能交接文档规则

本目录保存每个P0功能点的当前工作交接，使新Codex会话或开发者不依赖历史聊天即可恢复该功能的真实状态。

handoff是持续维护的工作恢复文档，不是不可修改的历史快照。提交历史由Git保存，长期技术取舍由`docs/DECISIONS.md`保存，阶段实验过程由`reports/`保存。

## 1. 文件索引

`docs/PROJECT_PLAN.md`中的每个P0功能点固定对应一份handoff：

| P0功能点 | handoff |
|---|---|
| P0-1 抽取数据合同与人工标注规范 | [P0-1-extraction-contract-and-annotation.md](P0-1-extraction-contract-and-annotation.md) |
| P0-2 JD结构化抽取 | [P0-2-structured-extraction.md](P0-2-structured-extraction.md) |
| P0-3 原子要求粒度验证与改进 | [P0-3-atomic-requirement-quality.md](P0-3-atomic-requirement-quality.md) |
| P0-4 跨JD原子要求归并与映射 | [P0-4-requirement-consolidation.md](P0-4-requirement-consolidation.md) |
| P0-5 分层评测与错误分析 | [P0-5-layered-evaluation.md](P0-5-layered-evaluation.md) |
| P0-6 高频岗位要求统计 | [P0-6-requirement-statistics.md](P0-6-requirement-statistics.md) |
| P0-7 统计结论证据追溯 | [P0-7-evidence-traceability.md](P0-7-evidence-traceability.md) |
| P0-8 扩充真实JD | [P0-8-jd-dataset-expansion.md](P0-8-jd-dataset-expansion.md) |
| P0-9 核心自动化测试 | [P0-9-automated-tests.md](P0-9-automated-tests.md) |
| P0-10 市场分析报告 | [P0-10-market-report.md](P0-10-market-report.md) |
| P0-11 演示与项目文档 | [P0-11-demo-and-documentation.md](P0-11-demo-and-documentation.md) |

新增、合并或拆分P0功能点时，必须同步调整本索引和对应handoff。普通缺陷、文案调整或子任务不新增handoff文件，而是更新受影响的现有文件。

## 2. 新会话快速启动

用户只需声明当前P0功能点和本次任务，不需要列出待读文档：

```text
继续 JD Skill Insight，当前开发 P0-X。

本次任务：<想完成的事情>。
约束：<可选限制>。
```

收到该指令后按以下顺序恢复上下文：

1. 按目标P0 handoff的`glossary_terms`定点读取`docs/GLOSSARY.md`对应章节及“跨阶段不变量”（术语语义存疑时读取全文），检查`git status`和最近提交；
2. 读取项目memory中的“当前P0状态摘要”（如存在，绑定同步commit），与`git log`对照；摘要过时时顺手更新memory；
3. 读取`docs/PROJECT_PLAN.md`中的目标P0功能行；
4. 完整读取目标P0 handoff；
5. 对`depends_on`列出的直接上游，先只读取“稳定事实”和“数据合同与不变量”；
6. 根据用户的具体任务，按`docs/CONTEXT_ROUTING.md`定点读取代码、测试、决策或数据摘要；
7. 完成修改前检查`affects`，只在下游状态、合同、入口或继续开发判断实际变化时更新对应handoff。

DeepSeek冷启动续作P0-3或P0-4时，在上述常规顺序后补读`docs/DEEPSEEK_CONTINUATION.md`，先输出只读上下文恢复报告；该指南不替代目标handoff，也不授权付费调用。

用户未说明具体任务时只汇报不修改的行为边界以`AGENTS.md`为准。

## 3. 依赖与影响规则

每份handoff使用以下字段表达跨功能关系：

- `depends_on`：开展当前功能前必须了解的直接上游P0功能点；
- `affects`：把当前功能列为直接依赖的下游P0功能点；
- `dependency_mode`：可选字段。默认是`fixed`；`task_scoped`表示按本次目标补充依赖，`selective`表示只读取已列依赖中与本次任务直接相关的部分。

读取和维护遵守以下边界：

1. 默认只展开一层`depends_on`，不递归加载完整依赖树；只有合同来源仍不明确时才继续向上游定点读取。
2. 读取上游handoff不等于读取其全部代码；先读稳定事实与不变量，仍不足时再读其他章节或目标代码片段。
3. `affects`不在会话启动时全文读取，只在修改完成前用于下游影响检查。
4. P0-11使用`selective`模式：依赖图保留全部上游关系，但会话启动只读取与本次文档或演示任务直接相关的上游handoff章节，不默认加载全部代码和报告。
5. P0-9使用`task_scoped`模式：按当前测试目标读取被测试功能的handoff，不把所有功能永久列为固定上游依赖。
6. `depends_on`和上游的`affects`必须双向一致，引用的P0编号必须存在，不得依赖自身。
7. 修改依赖关系时同步更新两端handoff；没有下游语义变化时，不机械改写所有`affects`文件。

## 4. 维护时机

出现以下任一情况时，更新受影响的handoff：

1. 功能开始开发、状态变化、暂停、恢复或达到验收标准；
2. 公共合同、核心流程、文件入口、依赖关系或禁止事项发生变化；
3. 一轮开发完成并产生可复现的验证结果；
4. 新发现的问题会影响下一会话的开发顺序或判断；
5. 跨功能变更影响多个P0功能点，此时同时维护所有受影响的handoff。

每个完成的开发阶段都必须更新对应handoff。handoff只保留恢复工作所需的当前事实，不记录每日流水、聊天过程或已经失效的操作步骤。

## 5. 元数据

每份handoff在标题后保存以下元数据：

```yaml
p0_id: "P0-N"
plan_item: "PROJECT_PLAN中的功能名称"
status: "completed | partial | in_progress | not_started"
baseline_commit: "本handoff内容最后一次与代码状态同步时的commit短哈希；只表示最近同步点，不保证等于最新提交，无法确认时为none"
verified_revision: "实际通过验证的commit，或working tree based on <baseline_commit>"
related_decisions: ["DEC编号或none"]
glossary_terms: ["本功能依赖的GLOSSARY术语"]
depends_on: ["直接上游P0编号，没有则为空列表"]
affects: ["直接下游P0编号，没有则为空列表"]
dependency_mode: "仅特殊功能填写，例如task_scoped或selective"
```

`status`必须与`docs/PROJECT_PLAN.md`一致。`verified_revision`只记录实际验证版本，不得虚构提交；未验证的草稿必须明确写为开发草稿。使用默认`fixed`模式时可以省略`dependency_mode`。

新会话启动以`git log`和`git status`为事实基准：发现handoff元数据或状态表述与git不符时，以git为准并顺手修正handoff（属于文档维护，无需单独列任务）；`baseline_commit`在每次状态同步时更新为当时的commit，不必在每次提交后立即跟进。

## 6. 内容结构

每份handoff使用以下一级标题：

1. `功能目标与边界`
2. `当前状态`
3. `稳定事实`
4. `实现与文件入口`
5. `数据合同与不变量`
6. `测试与验证`
7. `未完成事项与已知问题`
8. `继续开发入口`

未开始的功能也必须建立handoff，但只记录计划边界、依赖、预期入口和禁止提前假设的内容，不得把设计设想写成稳定事实。

## 7. 信息职责

各文档的唯一职责和唯一信息源见`docs/DOCUMENT_RULES.md`第1节；handoff维护恢复某个P0功能所需的当前工程上下文。handoff优先链接唯一信息源，不复制完整规则、Prompt、实验报告或数据内容；不得复制真实JD、完整模型JSON、完整人工标准答案、数据库内容、密钥或私人材料。

## 8. 开发完成检查

1. 检查Git范围并运行与风险匹配的验证；
2. 按`affects`检查下游，并更新所有实际受影响P0功能点的handoff；
3. 同步`PROJECT_PLAN.md`中的状态和验收摘要；
4. 检查术语、链接、敏感信息和待提交差异。

提交权限（Codex只准备建议提交范围、Summary和Description，commit和push由用户手动执行）以`AGENTS.md`为准。
