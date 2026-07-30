# JD标注规范索引

> 规范版本：1.4
> 适用范围：JD职责、岗位要求、原文证据与Golden Dataset

本目录是JD标注和数据语义的唯一完整规范。处理任务时只读取与当前目标对应的主题文档，不默认加载全部规范。

## 主题路由

| 当前任务 | 必读文档 |
|---|---|
| 标注或检查岗位职责 | [RESPONSIBILITIES.md](RESPONSIBILITIES.md) |
| 标注原子要求、类别、重要程度、熟练度或年限 | [REQUIREMENTS.md](REQUIREMENTS.md) |
| 处理证据、Golden、development/validation或评测数据 | [DATASET_EVALUATION.md](DATASET_EVALUATION.md) |

## 共同原则

1. 只依据JD明示内容，不补充行业常识或隐含技能。
2. 每条职责和要求只表达一个符合对应粒度规则的原子事实。
3. 每条结果绑定能够支持结论的连续原文证据。
4. 原始名称与后续标准技能分层保存，抽取阶段不提前归一。
5. 不确定的字段使用`unknown`或`null`，不能猜测。
6. Golden不能为迎合模型输出而修改。

## 当前数据合同

要求项由Schema V2保存以下核心字段：

| 字段 | 含义 |
|---|---|
| `raw_name` | JD中的原始要求概念，不做技能归一 |
| `category` | 要求类别 |
| `importance` | `must`、`preferred`、`mentioned`或`unknown` |
| `proficiency` | 原文明示的掌握程度 |
| `group_id` / `group_logic` | 独立要求或`any_of`任选组 |
| `min_years` / `max_years` / `years_text` | 年限下限、原文上限和完整表达 |
| `evidence` | 支持结论的连续JD原文 |
| `confidence` | 抽取置信度，人工确认Golden统一为`1.0` |

`canonical_name`、`relation_type`和`normalization_reason`属于后续技能本体阶段，不加入当前抽取合同。
