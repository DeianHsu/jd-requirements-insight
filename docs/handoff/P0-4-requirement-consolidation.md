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

开发中。`app/requirement_consolidation.py`和对应9项测试已随`b983db5`提交，但仍是开发草稿：只证明候选合同和确定性校验可以运行，不构成稳定下游合同，也没有完成真实语义评测或持久化。阶段1（数据输入装配）已完成：`app/consolidation.py`从数据库读取选定JD的最新抽取要求实例并保留来源定位，7项测试通过。

# 稳定事实

- P0-4输入是要求实例；`ResponsibilityItem`本身不参与要求归并。
- 职责文本中的候选人条件必须先由P0-2抽取为`RequirementItem`。
- 同义表达归并到同一标准要求项；`is_a`、`part_of`和`related_to`只建立关系，不触发归并。
- 同一表面词可以因证据上下文不同而映射到不同标准要求项。
- 代码必须领域无关，不硬编码Python、LangChain等具体领域技能。

# 实现与文件入口

- `app/requirement_consolidation.py`：开发草稿中的输入、输出、枚举和一致性校验。
- `app/consolidation.py`：装配P0-4归并输入，从数据库读取选定JD的最新抽取要求实例。
- `tests/test_requirement_consolidation.py`：草稿合同的9项确定性测试。
- `tests/test_consolidation.py`：输入装配的7项测试。
- `docs/GLOSSARY.md`：P0-4术语和跨阶段不变量。
- `docs/DECISIONS.md`中的DEC-009、DEC-018：分层保存与语料驱动方案。

# 数据合同与不变量

- `RequirementOccurrence`必须完整携带原始`RequirementItem`和来源定位。
- P0-4不得覆盖或删除`raw_name`、类别、重要程度、熟练度、逻辑组、年限、证据或抽取置信度。
- 每条要求实例必须且只能产生一个处理结果。
- 标准要求项ID和规范化名称不得重复；映射和关系不得引用未知ID。
- `related_to`反向重复视为同一关系；标准要求项必须有已映射来源。

# 测试与验证

草稿的9项测试与输入装配的7项测试通过，完整工作树50项测试通过，Ruff通过。尚未使用真实JD、完整人工标准答案或付费LLM执行P0-4业务验证。

# 未完成事项与已知问题

- 公共合同尚未冻结，也未进入数据库结构。
- LLM归并调用、Prompt、批次边界、持久化和CLI尚未实现。
- 尚未建立要求映射准确率、关系准确率和人工复核样例。
- 在小规模人工标准答案通过前，不运行全量要求归并。

# 继续开发入口

先读取本handoff、`docs/GLOSSARY.md`的要求归并章节、DEC-018、草稿实现和草稿测试。下一步应先冻结小规模输入输出合同和评测样例，再决定LLM调用与持久化设计。
