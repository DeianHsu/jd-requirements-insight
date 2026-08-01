# P0-5 分层评测与错误分析

```yaml
p0_id: "P0-5"
plan_item: "分层评测与错误分析"
status: "partial"
baseline_commit: "b983db5"
verified_revision: "working tree based on b983db5"
related_decisions: ["DEC-012", "DEC-013"]
glossary_terms: ["人工标准答案", "开发集", "回归集", "验证集", "名称代理指标", "证据存在率", "证据支持率"]
depends_on: ["P0-1", "P0-2"]
affects: ["P0-3", "P0-4", "P0-10", "P0-11"]
```

# 功能目标与边界

分别评测职责与要求发现、原子化、逻辑组、字段、证据和要求映射质量，并保留错误类型与失败案例。不能用单一总分代表系统质量。

# 当前状态

部分完成。完整JD人工标准答案校验和困难样例分层评测已经实现；`evaluate-cases`支持开发集、回归集和验证集。尚缺完整验证集、P0-4指标和证据支持性人工复核闭环。

# 稳定事实

- 已用于调参的数据属于开发集或回归集，不能重新宣称为未见验证集。
- 名称代理指标通过证据定位和确定性名称相似度进行一对一匹配，只用于版本比较。
- 无适用样本的指标显示`N/A`，不能显示为0%。
- 证据存在性是自动原文包含检查；证据支持性和最小性需要人工复核。
- 完整模型JSON只保存在本地，不在终端默认输出。

# 实现与文件入口

- `app/evaluation.py`：人工标准答案加载、名称匹配、分层指标和错误摘要。
- `app/cli.py`：`validate-golden`、`evaluate-extractions`、`evaluate-cases`。
- `docs/annotation/DATASET_EVALUATION.md`：数据集与指标规范。
- `tests/test_evaluation.py`、`tests/test_cli.py`：评测与输出约束测试。
- `reports/`：Prompt版本评测记录。

# 数据合同与不变量

- `GoldenExtractionRecord`绑定来源文件与期望抽取结果。
- 人工标准答案必须通过Pydantic和证据存在性校验。
- 查看验证集结果后如据此修改Prompt，该批数据不再承担下一轮正式验证职责。
- 证据存在率不得写成证据支持率。

# 测试与验证

当前工作树43项测试通过，Ruff通过。已记录的阶段结果包括开发集要求名称F1为100%、回归集职责F1为78.26%，两组证据存在率均为100%。

# 未完成事项与已知问题

- 新验证集尚未建立，现有结果不能证明稳定泛化能力。
- 完整JD人工标准答案数量未达到计划规模。
- 尚未实现要求映射准确率、关系准确率和证据支持率人工复核。

# 继续开发入口

评测规则先读`DATASET_EVALUATION.md`；实现问题读`app/evaluation.py`目标函数和对应测试。只读取目标数据集的安全摘要，不加载完整标注文件。
