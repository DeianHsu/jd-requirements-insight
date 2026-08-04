# 岗位要求规则（REQ / GROUP / FIELD）

> 只在标注候选人条件、检查要求原子化或解释抽取数据合同 V3 字段时读取本文。
> 规则 ID：`REQ-01`～`REQ-08`、`GROUP-01`～`GROUP-03`、`FIELD-01`～`FIELD-05`。修改规则语义必须同步更新本文件、Prompt 引用和测试。

## REQ-01 要求边界

要求表示公司对候选人的技能、经验、学历、专业或软能力条件。工资福利、办公地点、招聘者信息、投递提示、公司宣传口号和由常识推断的隐含技能不标注。

明确出现在任职要求中的“快速学习”“团队协作”等能力可以标为 `soft_skill`；只用于宣传时忽略。

## REQ-02 原子性

一条原子要求只能表达一个能够独立学习、分类、评价、匹配和统计的技能或条件。

```text
熟悉Python、RAG和LangChain。
```

拆为 `Python`、`RAG` 和 `LangChain`。学历、专业、经验年限、技术技能和软技能跨类别出现时也必须拆开（`REQ-03`）。

## REQ-03 跨类别拆分

技术技能、学历、专业、经验和软能力跨类别出现在同一句时必须拆开。

## REQ-04 固定复合要求

以下表达通常整体保留：

- 数据结构与算法；
- 问题分析与解决能力；
- Prompt Engineering；
- Function Calling；
- Retrieval-Augmented Generation；
- CI/CD；
- 大模型应用开发。

依次判断：

1. 是否为行业稳定复合表达；
2. 拆分后是否改变原意；
3. 各部分能否独立学习和评价；
4. 各部分能否形成有意义的独立统计。

固定复合要求或拆分后失真的表达整体保留；各部分可以独立匹配且具有统计价值时拆分。

## REQ-05 raw_name 保留

`raw_name` 保留 JD 中的原始业务含义，不做同义归一，也不在抽取阶段替换为标准要求项名称。例如“LangChain 使用经验”不能缩减成“LangChain”。

## REQ-06 禁止推断

- 不根据工资推断岗位级别；
- 不根据公司规模推断技术要求；
- 出现 RAG 时不自动补充 Embedding 和 Vector Database；
- 出现 Agent 时不自动补充 LangGraph；
- 出现后端开发时不自动补充 FastAPI；
- 没有明确年限时不猜测年限；
- 不根据常识修改重要程度或熟练度；
- 抽取阶段不把 JD 原词替换为标准要求项名称。

## REQ-07 直接修饰保留

具体技术名被“熟悉”“掌握”“使用经验”等条件直接修饰时，即使后面带“等”，也保留为原子要求。例如“熟悉LangChain、AutoGen等主流Agent开发框架”分别标注 `LangChain` 和 `AutoGen`；“等”只说明名单未穷尽，不能抹掉已经明确点名的技能，也不能把已点名技术改写成上位概念。

## REQ-08 模型名示例

具体模型名只用于修饰上位经验类型时，不单独标注模型。例如“有Llama、ChatGLM等大模型微调及应用落地经验”标注 `大模型微调经验` 和 `大模型应用落地经验`，Llama 与 ChatGLM 作为模型示例保留在证据中。

## GROUP-01 有限任选

只有原文给出明确候选项，并表达“至少一种”“任一”“或”等有限任选关系时，才把候选项放入同一个 `any_of` 组。判别优先级：

- 当“至少一种”“任一”“或”后直接列举具体技术名时，该列举是有限候选项，必须逐项拆成 `any_of` 成员，成员 `raw_name` 直接用具体技术名；
- 只有“完整上位概念 + 如/例如/括号引出 + 明显是非穷举示例”时，才保留上位概念为 `standalone`，不建立 `any_of`；“等”字本身不能决定是否为示例，关键是括号/如引出的内容是对上位概念的举例还是被候选条件直接修饰的具体技术名。

## GROUP-02 组结构

- 同一个 `any_of` 组至少包含两个成员；满足任意成员后，该组即视为满足。
- `standalone` 要求不得设置 `group_id`；`any_of` 要求必须设置 `group_id`。
- `group_id` 使用字符串（如 `group_1`），不允许输出数字。

普通要求使用：

```json
{"group_id": null, "group_logic": "standalone"}
```

“熟悉Go、Java、Python中至少一种”表示任选关系，拆为三个原子项，并使用共同 `group_id` 和 `group_logic = any_of`。

## GROUP-03 优先与并列

- “优先”“加分”只决定 `importance=preferred`，不产生 `any_of`；
- “和”“与”“并且”等并列连接默认保持 `standalone`（逐项独立条件）；
- 只有明确替代关系（GROUP-01：至少一种/任一/任选/之一/或）与“优先”同时出现时才建立 `preferred + any_of`。

正例：`有语言甲或语言乙经验者优先` → 两项 `preferred` 并共享同一个 `any_of` 组（有“或”替代关系；等价于“Python 或 Node.js 经验者优先”）。
反例：`有语言甲和语言乙经验者优先` → 两项 `preferred` 但均为 `standalone`（“和”是并列非替代，等价于“Python 和 Node.js 经验者优先”），不建 `any_of`。

## FIELD-01 category

| category | 定义 | 示例 |
|---|---|---|
| `programming_language` | 编程语言 | Python、Java、Go、C++ |
| `backend_engineering` | 后端服务、接口和微服务 | FastAPI、API设计、微服务 |
| `agent_framework` | Agent或LLM应用框架 | LangChain、LangGraph、AutoGen |
| `agent_capability` | Agent机制与核心能力 | Tool Calling、Memory、Workflow |
| `rag` | 完整RAG方法、方案或架构 | RAG、检索增强生成、RAG架构搭建 |
| `llm_application` | 通用LLM应用开发 | LLM API、Prompt Engineering |
| `model_training` | 训练、微调与对齐 | SFT、Fine-tuning、DPO |
| `ml_framework` | 机器学习框架 | PyTorch、TensorFlow |
| `retrieval` | 检索及RAG组成能力 | Embedding、Rerank、Vector Database |
| `deployment` | 部署、容器与运行环境 | Docker、K8s、Linux、TensorRT |
| `software_engineering` | 通用软件工程能力 | 测试、性能优化、系统设计 |
| `domain_knowledge` | 行业和专业领域知识 | 金融、法律、生物医药 |
| `education` | 学历条件 | 本科、硕士、博士 |
| `experience` | 工作、项目或行业经验 | 3年后端经验、线上系统经验 |
| `soft_skill` | 沟通、协作和学习能力 | 团队协作、快速学习、责任心 |
| `other` | 暂时无法归类 | 使用时记录无法归类原因 |

Embedding 和 Vector Database 与 RAG 相关，但类别是 `retrieval`，不能全部压成 `rag`。category 枚举是 AI/LLM/Agent/RAG 岗位领域配置，不是领域无关核心规则（见 `docs/annotation/README.md`）。

## FIELD-02 importance

- `must`：任职要求中的普通条件，以及明确使用“必须、要求、熟悉、掌握、精通”等措辞的条件；
- `preferred`：明确出现“优先、加分、更佳、有经验者优先”等措辞；
- `mentioned`：只在职责、场景或方向介绍中出现，没有要求候选人掌握；
- `unknown`：原文无法判断。

同一技能在基本要求中为 `must`、在加分项中又出现更高阶经验时，保留能表达差异的两项和两条证据，不能简单覆盖。

## FIELD-03 熟练程度（Schema V3 三级）

| JD 措辞 | proficiency |
|---|---|
| 没有明确程度词；仅出现使用经验、项目经验、有经验、参与过等表达 | `unknown` |
| 了解、理解、熟悉、能够使用、具备基础使用能力 | `basic` |
| 掌握、熟练、扎实、精通、专家级 | `advanced` |

- 不得根据项目经验、工作年限、技能出现位置或常识推断熟练程度；
- 原始程度词保留在 evidence，枚举只表达粗粒度岗位门槛；
- 不得输出 `understand`/`familiar`/`proficient`/`expert` 等旧五级值；
- `none`（完全不会）属于未来候选人个人能力层，不属于岗位要求：JD 未提出某项技能时根本没有对应 requirement，而不是 proficiency 为 `none`。

## FIELD-04 经验年限

| 原文 | min_years | max_years | years_text |
|---|---:|---:|---|
| 3年以上 | 3 | null | 3年以上 |
| 3～5年 | 3 | 5 | 3～5年 |
| 1年左右 | 1 | null | 1年左右 |
| 有项目经验 | null | null | null |
| 经验不限 | 0 | null | 经验不限 |

只提取原文明示的数字，不估算年限。`min_years` 是下限；`max_years` 只记录原文明示范围，不能自动解释为超过上限就不合格；只有原文明示排他限制时才用于筛选。年限上下限不得颠倒。

## FIELD-05 未知字段

不确定的字段使用 `unknown` 或 `null`，不得猜测。

证据与验证协议规则见 [VALIDATION.md](VALIDATION.md)，职责边界见 [RESPONSIBILITIES.md](RESPONSIBILITIES.md)。
