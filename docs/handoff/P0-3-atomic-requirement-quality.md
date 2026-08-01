# P0-3 原子要求粒度验证与改进

```yaml
p0_id: "P0-3"
plan_item: "原子要求粒度验证与改进"
status: "partial"
baseline_commit: "b983db5"
verified_revision: "working tree based on b983db5"
related_decisions: ["DEC-008", "DEC-016", "DEC-017"]
glossary_terms: ["原子要求", "固定复合要求", "困难样例", "名称代理指标"]
depends_on: ["P0-1", "P0-2", "P0-5"]
affects: ["P0-4", "P0-8", "P0-11"]
```

# 功能目标与边界

验证并改进P0-2产出的职责和原子要求粒度，确保复合条件不漏拆、不多拆，并保留正确证据与逻辑组。P0-3不定义新的要求类型，也不执行跨JD要求归并。

# 当前状态

部分完成。开发集的要求名称F1、职责F1、数量一致和`any_of`均为100%，类别准确率90.91%，熟练度准确率86.36%；回归集职责F1为78.26%，数量一致2/5。

# 稳定事实

- 原子化以能否独立学习、分类、评价、匹配或统计为判断基础。
- 固定复合要求或拆分后失真的表达整体保留。
- 非穷举示例不建立封闭`any_of`；被候选条件直接修饰的具体技术名逐项保留。
- 职责先识别独立交付，再判断多个动作是否属于同一职责。
- 开发集和回归集已经参与设计，不能宣称为新的未见验证集。

# 实现与文件入口

- `app/extraction.py`：当前Prompt和实验入口。
- `docs/annotation/REQUIREMENTS.md`、`RESPONSIBILITIES.md`：粒度规则。
- `tests/test_extraction.py`：Prompt边界和实验测试。
- `reports/PROMPT_V2_ANALYSIS.md`至`PROMPT_V2_3_1_ANALYSIS.md`：实验过程与指标。

# 数据合同与不变量

- 原子化结果仍使用P0-1的`RequirementItem`和`ResponsibilityItem`。
- 多个原子项可以共享同一连续证据，但不得拼接不连续文本。
- 不按顿号、“和”或“与”机械拆分。
- 不因模型当前输出修改人工标准答案。

# 测试与验证

当前工作树完整测试43项通过，Ruff通过。P0-3主要由Prompt边界测试、困难样例分层评测和历史Prompt报告支撑。

# 未完成事项与已知问题

- 回归集职责召回和数量一致尚未达到验收目标。
- 熟练度准确率仍低于目标。
- 需要在单次重组与职责/要求分步抽取之间选择下一种架构，并使用新的未见验证集评测。

# 继续开发入口

先读取本handoff、两个粒度规范、`app/extraction.py`中的Prompt目标段和`tests/test_extraction.py`相关测试；只读取一个指定困难样例或错误摘要，不加载完整标注数据。
