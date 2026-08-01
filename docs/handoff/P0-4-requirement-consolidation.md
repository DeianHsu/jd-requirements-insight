# P0-4 跨JD原子要求归并与映射

```yaml
p0_id: "P0-4"
plan_item: "跨JD原子要求归并与映射"
status: "in_progress"
baseline_commit: "b983db5"
verified_revision: "working tree based on b983db5; draft contract only"
related_decisions: ["DEC-009", "DEC-018"]
glossary_terms: ["要求实例", "标准要求项", "要求归并", "要求映射", "要求关系"]
depends_on: ["P0-1", "P0-3", "P0-5"]
affects: ["P0-6", "P0-7", "P0-8", "P0-10", "P0-11"]
```

# 功能目标与边界

汇总选定范围内全部`RequirementItem`对应的要求实例，把指向同一招聘条件的表达归并到标准要求项，并保存可追溯映射及必要的非同义关系。减少的是跨JD统计口径，不删除来源记录。

# 当前状态

开发中。`app/requirement_consolidation.py`和对应9项测试已随`b983db5`提交，但仍是开发草稿：只证明候选合同和确定性校验可以运行，不构成稳定下游合同，也没有完成真实语义评测或持久化。阶段1（数据输入装配）已完成：`app/consolidation.py`从数据库读取选定JD的最新抽取要求实例并保留来源定位，7项测试通过。阶段2（LLM归并调用与Prompt v1）已完成：客户端、领域无关Prompt v1、解析与有限重试闭环，9项测试通过。阶段2.8真实试跑已完成：DeepSeek V4 Flash按prompt:1.0将当前5份JD的149条要求实例归并为125个标准要求项、17条关系，全部mapped并通过合同与覆盖校验；试跑结果未持久化（阶段4实现后重跑）。阶段3（归并执行与校验闭环）已完成：批量执行入口、失败隔离与汇总摘要，5项测试通过。阶段4（持久化与幂等）已完成：4张归并表（批次/标准要求项/映射/关系）、按范围键+归并器版本幂等保存、映射可追溯回原始要求，6项测试通过。阶段5（CLI）已完成：`consolidate-requirements`命令（--job-id/--all/--max-attempts，互斥校验，LLM配置检查，错误退出码），3项CLI测试通过。

# 稳定事实

- P0-4输入是要求实例；`ResponsibilityItem`本身不参与要求归并。
- 职责文本中的候选人条件必须先由P0-2抽取为`RequirementItem`。
- 同义表达归并到同一标准要求项；`is_a`、`part_of`和`related_to`只建立关系，不触发归并。
- 同一表面词可以因证据上下文不同而映射到不同标准要求项。
- 代码必须领域无关，不硬编码Python、LangChain等具体领域技能。

# 实现与文件入口

- `app/requirement_consolidation.py`：开发草稿中的输入、输出、枚举和一致性校验。
- `app/consolidation.py`：P0-4归并执行：装配输入、LLM客户端与Prompt v1、解析与有限重试。
- `tests/test_requirement_consolidation.py`：草稿合同的9项确定性测试。
- `tests/test_consolidation.py`：输入装配的7项测试。
- `tests/test_consolidation_client.py`：归并LLM客户端、Prompt领域无关性与重试闭环的9项测试。
- `tests/test_consolidation_run.py`：批量归并执行与失败隔离的5项测试。
- `tests/test_consolidation_persist.py`：归并持久化与幂等的6项测试。
- `docs/GLOSSARY.md`：P0-4术语和跨阶段不变量。
- `docs/DECISIONS.md`中的DEC-009、DEC-018：分层保存与语料驱动方案。

# 数据合同与不变量

- `RequirementOccurrence`必须完整携带原始`RequirementItem`和来源定位。
- P0-4不得覆盖或删除`raw_name`、类别、重要程度、熟练度、逻辑组、年限、证据或抽取置信度。
- 每条要求实例必须且只能产生一个处理结果。
- 标准要求项ID和规范化名称不得重复；映射和关系不得引用未知ID。
- `related_to`反向重复视为同一关系；标准要求项必须有已映射来源。

# 测试与验证

草稿的9项、输入装配的7项、归并客户端的9项、批量执行的5项、持久化的6项与CLI的3项测试通过，完整工作树73项测试通过，Ruff通过。阶段2.8已用真实LLM完成一次小规模试跑（约1次API调用，149实例→125标准要求项、17关系，全部mapped）。

# 未完成事项与已知问题

- 公共合同尚未冻结，也未进入数据库结构。
- 批次边界：当前语料规模（约150条实例）单次调用已验证可行，暂不分批；待P0-8数据扩充后再评估。
- CLI已完成；尚未建立要求映射准确率、关系准确率和人工复核样例（阶段6）。
- 尚未建立要求映射准确率、关系准确率和人工复核样例。
- 在小规模人工标准答案通过前，不运行全量要求归并。

# 继续开发入口

先读取本handoff、`docs/GLOSSARY.md`的要求归并章节、DEC-018、草稿实现和草稿测试。下一步应先冻结小规模输入输出合同和评测样例，再决定LLM调用与持久化设计。
