# P0-2 JD结构化抽取

```yaml
p0_id: "P0-2"
plan_item: "JD结构化抽取"
status: "completed"
baseline_commit: "1f50451"
verified_revision: "working tree based on 1f50451"
related_decisions: ["DEC-007", "DEC-012", "DEC-014", "DEC-016", "DEC-017"]
glossary_terms: ["结构化抽取", "抽取器版本", "证据存在性", "职责", "原子要求"]
depends_on: ["P0-1"]
affects: ["P0-3", "P0-5", "P0-7", "P0-8", "P0-11"]
```

# 功能目标与边界

以一份完整JD为输入，调用兼容OpenAI接口的LLM，按抽取数据合同输出职责和原子要求，并执行结构、跨字段和证据存在性校验。P0-2不负责跨JD要求归并、频率统计或个人匹配。

# 当前状态

已完成基础验收。DeepSeek V4 Flash已按Prompt V2.3.1与抽取数据合同V2完成5份真实JD重抽取，最终失败0份；当前Prompt因职责回归冻结，粒度改进转由P0-3继续处理。

# 稳定事实

- 客户端使用`temperature=0`和JSON Object模式。
- 模型响应先解析为`JobExtractionResult`，再执行证据存在性校验。
- 失败允许有限重试，并把安全的校验反馈加入下一次请求。
- 同一JD与同一抽取器版本重复执行时保持幂等。
- 抽取器版本由模型、Prompt和抽取数据合同版本共同确定。
- 默认开发批次有限；全量抽取必须显式使用`--all`。

# 实现与文件入口

- `app/extraction.py`：Prompt、模型调用、解析、校验、重试和持久化。
- `app/config.py`：LLM配置。
- `app/cli.py`：`extract-jds`和`list-extractions`。
- `tests/test_extraction.py`、`tests/test_cli.py`：抽取与CLI测试。
- `reports/PROMPT_V2_3_1_ANALYSIS.md`：当前冻结版本的实验结论。

# 数据合同与不变量

- 不得接受无法通过Pydantic合同或证据存在性校验的模型结果。
- 不得默认输出完整模型JSON或JD正文到终端日志。
- 不得用Prompt在抽取阶段提前完成同义要求归并。
- 未通过小规模验证前不得触发昂贵的全量模型调用。

# 测试与验证

当前工作树完整测试43项通过，Ruff通过。既有测试覆盖Prompt边界、合法响应、证据缺失、有限重试、抽取器版本幂等、开发批次限制和实验入口。

# 未完成事项与已知问题

- 单次混合抽取存在职责与要求之间的跨任务干扰或模型非确定性。
- Prompt V2.3.1不是职责粒度问题的最终版本；不要继续无边界追加规则。

# 继续开发入口

抽取调用或持久化问题从`app/extraction.py`目标函数及对应测试开始。职责/要求粒度问题转到P0-3 handoff；评测问题转到P0-5 handoff。
