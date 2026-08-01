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

部分完成（暂停中）。开发集的要求名称F1、职责F1、数量一致和`any_of`均为100%，类别准确率90.91%，熟练度准确率86.36%；回归集职责F1为78.26%，数量一致2/5。V2.3.1报告决定暂停继续堆叠单体Prompt规则。两段式（发现/判断）实验已归档：回归集职责F1提升至92.31%（超越基线78.26%），开发集要求F1未追平（峰值91.95% vs 100%）；详见`reports/P0_3_TWO_STAGE_EXPERIMENT.md`。

# 稳定事实

- 原子化以能否独立学习、分类、评价、匹配或统计为判断基础。
- 固定复合要求或拆分后失真的表达整体保留。
- 非穷举示例不建立封闭`any_of`；被候选条件直接修饰的具体技术名逐项保留。
- 职责先识别独立交付，再判断多个动作是否属于同一职责。
- 开发集和回归集已经参与设计，不能宣称为新的未见验证集。

# 实现与文件入口

- `app/extraction.py`：当前Prompt和实验入口。
- `app/extraction_two_stage.py`：两段式实验模块（发现段/判断段、中间合同、覆盖检查；已归档，不接入正式抽取）。
- `tests/test_extraction_two_stage.py`：两段式合同、覆盖与重试测试（8项）。
- `docs/annotation/REQUIREMENTS.md`、`RESPONSIBILITIES.md`：粒度规则。
- `tests/test_extraction.py`：Prompt边界和实验测试。
- `reports/PROMPT_V2_ANALYSIS.md`至`PROMPT_V2_3_1_ANALYSIS.md`：历史实验；`reports/P0_3_TWO_STAGE_EXPERIMENT.md`：两段式归档。

# 数据合同与不变量

- 原子化结果仍使用P0-1的`RequirementItem`和`ResponsibilityItem`。
- 多个原子项可以共享同一连续证据，但不得拼接不连续文本。
- 不按顿号、“和”或“与”机械拆分。
- 不因模型当前输出修改人工标准答案。

# 测试与验证

当前工作树完整测试43项通过，Ruff通过。P0-3主要由Prompt边界测试、困难样例分层评测和历史Prompt报告支撑。

# 未完成事项与已知问题

- 回归集职责召回和数量一致尚未达到验收目标（两段式已改善，V2.3.1仍为正式Prompt）。
- 熟练度准确率仍低于目标。
- 两段式要求侧未追平V2.3.1；恢复时从v0.5继续，补要求规则细节1-2轮后可预期追平。
- 新验证集待创建（当前开发集/回归集已参与设计）。

# 继续开发入口

先读取本handoff、`reports/P0_3_TWO_STAGE_EXPERIMENT.md`、两个粒度规范、`app/extraction_two_stage.py`和`app/extraction.py`中V2.3.1的Prompt规则。恢复条件：新领域冷启动或数据扩充后V2.3.1质量不达标时启用两段式（从v0.5继续迭代要求侧）；正式验收需另建未见验证集。
