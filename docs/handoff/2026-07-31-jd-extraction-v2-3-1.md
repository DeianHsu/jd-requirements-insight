# JD结构化抽取 V2.3.1 阶段交接

```yaml
stage_id: "legacy-extraction-v2.3.1"
plan_item: "P0-1 Schema与人工标注规范；P0-2 JD结构化抽取；P0-3 原子化（部分）；P0-5 Evaluation（部分）"
status: "frozen"
created_at: "2026-07-31"
baseline_commit: "03779e7"
verified_revision: "89fc9ce"
next_stage: "P0-4 技能本体与规范化映射"
related_decisions: ["DEC-007", "DEC-008", "DEC-009", "DEC-010", "DEC-011", "DEC-012", "DEC-014", "DEC-016", "DEC-017"]
```

# 阶段目标

本阶段建立JD结构化抽取的Schema V2、Prompt V2.3.1、证据校验、版本化持久化和分层Evaluation，并在发现职责回归后冻结当前Prompt，作为进入“技能本体与归一化”前的可复现基线。

本handoff建立于阶段化流程启用之前，因此兼容记录多个相邻计划项；从下一阶段开始按一个计划功能点建立一份handoff。对应代码提交：

- `d5e27ed`：升级Schema V2并完善Prompt与分层Evaluation；
- `89fc9ce`：优化JD抽取迭代流程并降低LLM验证成本。

# 已完成内容

- Schema V2已支持职责、原子要求、重要程度、熟练度、任选逻辑组、经验范围和连续原文证据。
- Prompt V2.3.1已覆盖职责/要求区分、并列原子项、非穷举示例、具体技术名、`any_of`和年限表达。
- LLM结果通过Pydantic结构校验、跨字段约束和证据存在性校验后才写入SQLite；失败可有限重试，同一JD与抽取器版本保持幂等。
- Evaluation已支持development、regression和validation分组；CLI支持少量默认抽取、`--job-id`定向验证、`--all`显式回归和有限错误摘要。
- 输入Schema已压缩，Schema部分由3365字符降至2241字符。
- `raw_name`继续保存完整业务含义，技能标准化留给归一层；任选关系、年限和原子化语义以相关DEC与标注规范为准。
- Prompt V2.3.1因`case_012`职责回归而冻结，单次重组和职责隔离实验均未形成可确认的新版本。

# 修改文件

- `app/schemas.py`：定义Schema V2及`any_of`、经验范围等跨字段约束。
- `app/models.py`：保存职责、原子要求、逻辑组、年限和抽取版本。
- `app/database.py`：兼容旧SQLite结构并补充V2字段迁移。
- `app/extraction.py`：保存Prompt V2.3.1、压缩模型Schema、执行抽取、证据校验、有限重试、幂等持久化和非正式架构实验。
- `app/evaluation.py`：实现Golden校验和困难样例分层指标。
- `app/cli.py`：提供抽取范围控制、结果查看和分层评测命令。
- `docs/annotation/`：保存职责、要求和数据集评测的人工语义规范。
- `tests/test_schemas.py`、`tests/test_database.py`、`tests/test_extraction.py`、`tests/test_evaluation.py`、`tests/test_cli.py`：覆盖Schema、迁移、抽取、评测和CLI行为。

# 当前架构状态

```text
Markdown JD → 导入校验与SHA-256去重 → SQLite
→ Prompt + 压缩JSON Schema + JD正文 → OpenAI兼容LLM
→ Pydantic与证据校验 → 按模型/Prompt/Schema版本持久化
→ Golden Dataset分层Evaluation
```

核心模块职责见`app/README.md`。当前正式抽取版本为Prompt `2.3.1`、Schema `2.0`；`cli.py`提供本地入口，`extraction.py`负责模型调用与校验，`evaluation.py`负责Golden评测。真实JD、完整模型JSON和数据库均保留在本地忽略目录。

# 数据契约

- 输入：带YAML Front Matter的Markdown JD，至少包含公司、岗位、采集日期、来源文件和正文。
- 输出：`JobExtractionResult`，包含岗位方向、级别、职责和原子要求；定义见`app/schemas.py`。
- 要求项保留`raw_name`、分类、重要程度、熟练度、逻辑组、经验范围、连续证据和置信度。
- 关键约束：禁止额外字段；`standalone`不能带组ID；`any_of`必须成组且至少两个成员；年限范围合法；证据必须存在于JD正文。

# 测试与验证

阶段交接前执行：

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check --no-cache .
```

结果：34项测试全部通过，Ruff全部通过。存在一个来自FastAPI TestClient依赖链的Starlette弃用警告，不影响当前功能。

已验证场景包括Schema V2逻辑组与年限约束、旧数据库迁移、证据缺失拒绝、失败重试、版本幂等、Prompt规则边界、开发批次限制、Evaluation数据分组和错误摘要限制。

# 已知问题与限制

- Prompt V2.3.1的development要求名称和逻辑组表现较好，但proficiency为86.36%。
- regression职责F1为78.26%，数量一致为2/5；`case_012`期望3项职责但当前结果为0项。
- 单次重组实验产生非法category；职责隔离实验输入显著减少，但没有恢复`case_012`。
- 当前没有新的未见validation样例，不能证明Prompt具有稳定泛化能力。
- 真实数据只有5份JD，暂时不足以生成有代表性的正式市场结论。
- 技能本体、确定性归一、高频统计和证据查询尚未实现。

# 下一阶段任务

下一阶段目标是实现第一版小型AI应用岗位技能本体与确定性归一化，不调用付费LLM。

建议顺序：

1. 从少量人工审核要求确定15至25个种子技能及稳定ID。
2. 定义exact、alias、abbreviation、parent_child、component和related等关系语义。
3. 定义mapped、unmapped和review_required状态，禁止未知要求被强制归一。
4. 实现确定性本体加载与映射函数，保留`raw_name`、证据和映射依据。
5. 用8至12个代表性人工样例完成小规模验证。
6. 小规模通过后，对全部人工审核要求执行回归并计算归一指标。

验收标准：同义词和缩写稳定映射；组成、相关和上下位关系不被误合并；未知要求可安全保留；相同输入结果一致；原始名称与证据不被覆盖；相关测试和Ruff通过。

# 下一阶段注意事项

- 不继续修改Prompt V2.3.1，不运行5份JD全量DeepSeek抽取。
- 不把模型输出直接当作本体事实，优先使用人工审核的Golden要求。
- 不在归一阶段覆盖`raw_name`、importance、proficiency、逻辑组、年限或evidence。
- 不把parent_child、component或related关系当作alias合并。
- 不删除现有Prompt实验代码，除非另有明确决定。
- 未完成归一全量回归前，不把项目计划中的“技能本体与规范化映射”标记完成。
