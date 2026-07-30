# 阶段目标

本阶段建立JD结构化抽取的Schema V2、Prompt V2.3.1、证据校验、版本化持久化和分层Evaluation，并在发现职责回归后冻结当前Prompt，作为进入“技能本体与归一化”前的可复现基线。

对应提交：

- `d5e27ed`：升级Schema V2并完善Prompt与分层Evaluation；
- `89fc9ce`：优化JD抽取迭代流程并降低LLM验证成本。

# 已完成内容

## 已实现功能

- Schema V2支持职责、原子要求、重要程度、熟练度、任选逻辑组、经验上下限和连续原文证据。
- Prompt V2.3.1区分职责与要求，并处理并列原子项、非穷举示例、具体技术名称、`any_of`和经验表达。
- 抽取结果经过Pydantic结构校验和原文证据存在性校验，失败时可以有限重试。
- 模型、Prompt和Schema共同组成抽取器版本，同一JD和版本重复执行时幂等跳过。
- Evaluation支持development、regression和validation分组，并输出原子项、字段、逻辑组、年限和证据指标。
- CLI默认最多抽取3份JD，支持`--job-id`定向验证和`--all`显式全量回归；评测默认只显示有限错误摘要。
- 模型输入Schema删除重复标题与说明并压缩JSON，Schema部分字符数由3365降至2241。

## 核心逻辑

抽取流程先由LLM按Schema返回JSON，再由确定性代码负责结构、跨字段关系和证据检查，只有合法结果可以写入SQLite。真实模型负责语义抽取，Pydantic、数据库约束和Evaluation负责可验证性。

## 关键设计决策

- 招聘JD分析属于信息抽取任务，必须保留原始要求和连续证据，不能只生成总结或分数。
- `raw_name`保留“LangChain使用经验”等完整业务含义，技能标准化留给后续归一层。
- `group_id + group_logic=any_of`只表达原文明示的任选关系；独立要求使用`standalone`。
- Prompt V2.3.1暂时冻结。单次重组和职责隔离实验未修复`case_012`，不进入全量回归或正式版本确认。
- 后续开发统一采用“静态检查→小规模验证→全量回归→版本确认”，付费LLM全量调用只允许出现在全量回归阶段。

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

## 模块结构

```text
ingestion.py    Markdown JD解析、校验与去重
schemas.py      输入与抽取输出的数据合同
models.py       SQLite ORM持久化结构
database.py     Engine、Session和兼容迁移
extraction.py   Prompt、LLM调用、校验与抽取保存
evaluation.py   Golden与困难样例指标
cli.py          本地批处理入口和摘要输出
```

## 数据流

```text
Markdown JD
→ Front Matter与正文校验
→ SHA-256去重并写入SQLite
→ Prompt规则 + 压缩JSON Schema + JD原文
→ OpenAI兼容LLM返回JSON
→ Pydantic结构校验
→ 连续原文证据校验
→ 按模型、Prompt、Schema版本幂等保存
→ Golden Dataset分层Evaluation
```

## 输入输出与依赖

- 输入是带YAML Front Matter的Markdown JD，真实文件位于本地忽略目录。
- LLM输出必须符合`JobExtractionResult`，完整JSON只保存在本地数据库，不默认打印。
- `cli.py`依赖配置、数据库、抽取和评测模块；`extraction.py`依赖Schema与ORM；`evaluation.py`依赖Schema、原文和人工case。
- 当前正式版本为Prompt `2.3.1`、Schema `2.0`。

# 数据契约

## 输入

JD导入至少需要公司、岗位、采集日期、来源文件和正文；正文用于抽取及证据校验，来源文件用于关联Golden记录。

## 输出

`JobExtractionResult`包含：

- `role_family`：岗位方向枚举；
- `seniority`：岗位级别枚举；
- `responsibilities`：职责数组，每项包含`name`和连续原文`evidence`；
- `requirements`：原子要求数组。

每个`RequirementItem`包含：

- `raw_name`：保留原始业务含义的要求名称；
- `category`：要求类别；
- `importance`：must、preferred、mentioned或unknown；
- `proficiency`：unknown、understand、familiar、proficient或expert；
- `group_id`、`group_logic`：独立或任选逻辑；
- `min_years`、`max_years`、`years_text`：经验范围及原文表达；
- `evidence`：JD原文中的连续证据；
- `confidence`：0到1之间的模型置信度。

主要约束：禁止额外字段；文本不能为空；`standalone`不能带`group_id`；`any_of`必须带组ID且同组至少两个成员；年限为0到50且上限不能小于下限；证据必须存在于JD原文。

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
