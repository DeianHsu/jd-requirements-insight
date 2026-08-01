# P0-1 抽取数据合同与人工标注规范

```yaml
p0_id: "P0-1"
plan_item: "抽取数据合同与人工标注规范"
status: "completed"
baseline_commit: "b983db5"
verified_revision: "working tree based on b983db5"
related_decisions: ["DEC-007", "DEC-008", "DEC-009", "DEC-010", "DEC-011", "DEC-013", "DEC-014", "DEC-017"]
glossary_terms: ["抽取数据合同", "职责", "原子要求", "人工标准答案", "证据存在性"]
depends_on: []
affects: ["P0-2", "P0-3", "P0-4", "P0-5", "P0-8", "P0-11"]
```

# 功能目标与边界

定义JD结构化抽取的输入、输出、字段语义、原子化规则、证据边界和人工标注方法。P0-1只定义合同与规则，不负责模型调用、跨JD要求归并或统计。

# 当前状态

已完成。人工标注规范1.4、Pydantic抽取数据合同V2、SQLite对应字段和旧数据库增量迁移已经建立并通过测试。

# 稳定事实

- `JobDocument`表示一份完整JD输入，保留已知元数据、正文和未识别元数据。
- `JobExtractionResult`输出岗位方向、岗位级别、职责和原子要求。
- `ResponsibilityItem`表示入职后工作；`RequirementItem`表示候选人条件，两者不得混用。
- 原子要求保留`raw_name`、类别、重要程度、熟练度、逻辑组、年限、连续证据和抽取置信度。
- `group_id + group_logic`表达`standalone`与`any_of`；年限使用下限、原文明示上限和完整表达。
- 人工标准答案不得为迎合模型输出而修改。

# 实现与文件入口

- `app/schemas.py`：Pydantic输入与抽取数据合同。
- `app/models.py`：职责、要求和抽取结果的ORM结构。
- `app/database.py`：建表及可重复执行的SQLite增量迁移。
- `docs/annotation/README.md`：标注规范入口。
- `docs/annotation/RESPONSIBILITIES.md`、`REQUIREMENTS.md`、`DATASET_EVALUATION.md`：主题规则。
- `tests/test_schemas.py`、`tests/test_database.py`：合同和数据库结构测试。

# 数据合同与不变量

- 抽取结果禁止额外字段。
- `standalone`不能带组ID；`any_of`必须成组且至少两个成员。
- 年限范围必须合法，旧`years_required`只作为兼容输入。
- 证据必须是JD中的连续原文。
- P0-4不得覆盖P0-1定义的原子要求字段。

# 测试与验证

当前工作树完整测试43项通过，Ruff通过。P0-1相关测试覆盖逻辑组、年限、旧字段兼容和数据库结构迁移。

# 未完成事项与已知问题

P0-1本身已经达到计划验收标准。后续若修改字段或标注语义，必须同步更新Pydantic合同、ORM、迁移、标注规范、测试及本handoff。

# 继续开发入口

先读取`app/schemas.py`目标类型和对应标注主题，再读取相关测试。只有出现跨字段或长期数据语义变化时才新增决策记录。
