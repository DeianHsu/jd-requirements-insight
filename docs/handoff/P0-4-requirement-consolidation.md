# P0-4 跨JD原子要求归并与映射

```yaml
p0_id: "P0-4"
plan_item: "跨JD原子要求归并与映射"
status: "completed"
baseline_commit: "efac164"
verified_revision: "working tree based on efac164"
related_decisions: ["DEC-009", "DEC-018"]
glossary_terms: ["要求实例", "标准要求项", "要求归并", "要求映射", "要求关系"]
depends_on: ["P0-1", "P0-3", "P0-5"]
affects: ["P0-5", "P0-6", "P0-7", "P0-8", "P0-10", "P0-11"]
```

# 功能目标与边界

汇总选定范围内全部`RequirementItem`对应的要求实例，把指向同一招聘条件的表达归并到标准要求项，并保存可追溯映射及必要的非同义关系。减少的是跨JD统计口径，不删除来源记录。

# 当前状态

已完成。七个阶段全部交付：输入装配（`app/consolidation.py`）→ LLM归并调用与Prompt迭代至v1.4（领域无关）→ 批量执行与失败隔离 → 4张归并表持久化与幂等 → `consolidate-requirements` CLI → 人工标准答案评测（`app/consolidation_evaluation.py`）→ 真实全量归并验证。真实评测基线（DeepSeek V4 Flash，13实例人工标准答案，4轮Prompt迭代）：映射准确率84.62%（11/13）、关系准确率80%（4/5）；全量归并149实例→101标准项、25处归并、关系67条，映射理由与来源可追溯。

# 稳定事实

- P0-4输入是要求实例；`ResponsibilityItem`本身不参与要求归并。
- 职责文本中的候选人条件必须先由P0-2抽取为`RequirementItem`。
- 同义表达归并到同一标准要求项；`is_a`、`part_of`和`related_to`只建立关系，不触发归并。
- 同一表面词可以因证据上下文不同而映射到不同标准要求项。
- 代码与Prompt必须领域无关，不硬编码具体领域技能（有自动化测试断言）。
- 归并按范围键（`all`或`job_ids=...`）+ 归并器版本（model|prompt|schema）幂等保存，规则变化保留新旧结果。

# 实现与文件入口

- `app/requirement_consolidation.py`：归并合同的输入、输出、枚举和一致性校验（已稳定，9项测试）。
- `app/consolidation.py`：归并执行：装配输入、LLM客户端与Prompt v1.4、解析与有限重试、批量执行、幂等持久化。
- `app/consolidation_evaluation.py`：映射/关系/未映射准确率评测（名称规范化跨ID匹配，N/A语义）。
- `app/models.py`：`JobConsolidation`、`CanonicalRequirementRecord`、`RequirementMappingRecord`、`RequirementRelationRecord`四张表（幂等唯一约束、追溯外键）。
- `app/cli.py`：`consolidate-requirements`与`list-consolidations`命令。
- `tests/test_requirement_consolidation.py`：合同9项；`tests/test_consolidation.py`：装配7项；`tests/test_consolidation_client.py`：客户端9项；`tests/test_consolidation_run.py`：批量5项；`tests/test_consolidation_persist.py`：持久化6项；`tests/test_consolidation_evaluation.py`：评测7项；`tests/test_cli.py`：CLI归并3项。
- `data/consolidation_cases.json`：本地私有评测样例（含真实JD证据，.gitignore排除，不进公开仓库）。
- `docs/GLOSSARY.md`：P0-4术语和跨阶段不变量。
- `docs/DECISIONS.md`中的DEC-009、DEC-018：分层保存与语料驱动方案。

# 数据合同与不变量

- `RequirementOccurrence`必须完整携带原始`RequirementItem`和来源定位。
- P0-4不得覆盖或删除`raw_name`、类别、重要程度、熟练度、逻辑组、年限、证据或抽取置信度。
- 每条要求实例必须且只能产生一个处理结果。
- 标准要求项ID和规范化名称不得重复；映射和关系不得引用未知ID。
- `related_to`反向重复视为同一关系；标准要求项必须有已映射来源。

# 测试与验证

全量81项测试通过（合同9 + 装配7 + 客户端9 + 批量5 + 持久化6 + 评测7 + CLI归并与列表4 + 其余34），Ruff通过。真实评测4轮Prompt迭代（v1.0 46%/0% → v1.1 77%/60% → v1.2 85%/80% → v1.4 85%/80%基线）；全量归并成功持久化（149实例→101标准项，抽查归并合理、理由可追溯）。

# 未完成事项与已知问题

- any_of任选组成员偶发被归并（如656"Python"被并入上位概念、C++/Java/Go被顿号拼接），影响统计口径，P0-6需关注。
- "微调经验"类具体活动与"落地经验"的关系模型判为`related_to`而人工标准答案判`part_of`，属可讨论的判断分歧。
- 评测样例为开发集性质（已用于调参），正式的未见验证集与要求映射指标挂接属P0-5职责。
- 批次边界：当前语料规模（约150条实例）单次调用可行，暂不分批；待P0-8数据扩充后再评估。
- 全量归并单次调用耗时5-8分钟，且新Prompt版本首次调用常失败需重试一次（疑似输出过长或API偶发，未诊断根因）。
- 关系类型互斥无合同约束：`is_a`/`part_of`与`related_to`语义互斥（GLOSSARY），但合同只禁止同三元组重复，不禁止两者并存（真实数据当前0冲突）。
- `review_required`/`unmapped`路径在真实数据中未被触发（评测全部mapped），仅有确定性测试覆盖。
- Prompt v1.5已补充年限/门槛差异归并规则（"3年以上"与"5年以上"经验不得归并），未重跑评测（语料仅1个年限实例，v1.4已验证其正确处理）；评测基线仍为v1.4。

# 继续开发入口

下游P0-6（高频统计）直接读取`canonical_requirements`/`requirement_mappings`；P0-5在分层评测中接入`consolidation_evaluation`的要求映射指标并建立未见验证集；P0-8数据扩充后重新评估批次边界与全量耗时。
