# 岗位要求标注规范

> 只在标注候选人条件、检查要求原子化或解释Schema V2字段时读取本文。

## 1. 要求边界

要求表示公司对候选人的技能、经验、学历、专业或软能力条件。工资福利、办公地点、招聘者信息、投递提示、公司宣传口号和由常识推断的隐含技能不标注。

明确出现在任职要求中的“快速学习”“团队协作”等能力可以标为`soft_skill`；只用于宣传时忽略。

## 2. 原子要求

一条原子要求只能表达一个能够独立学习、分类、评价、匹配和统计的技能或条件。

```text
熟悉Python、RAG和LangChain。
```

拆为`Python`、`RAG`和`LangChain`。学历、专业、经验年限、技术技能和软技能跨类别出现时也必须拆开。

## 3. 固定复合概念

以下表达通常整体保留：

- 数据结构与算法；
- 问题分析与解决能力；
- Prompt Engineering；
- Function Calling；
- Retrieval-Augmented Generation；
- CI/CD；
- 大模型应用开发。

依次判断：

1. 是否为行业稳定概念；
2. 拆分后是否改变原意；
3. 各部分能否独立学习和评价；
4. 各部分能否形成有意义的独立统计。

稳定概念或拆分后失真的表达整体保留；各部分可以独立匹配且具有统计价值时拆分。

## 4. 同时要求与任选组

“熟悉Python和RAG”表示两项都需要，拆为两个`standalone`要求。

“熟悉Go、Java、Python中至少一种”表示任选关系，拆为三个原子项，并使用共同`group_id`和`group_logic = any_of`。同一个`any_of`组至少包含两个成员；满足任意成员后，该组即视为满足。

普通要求使用：

```json
{"group_id": null, "group_logic": "standalone"}
```

“Python/Node.js优先”拆为两个`preferred`成员，并放入同一个`any_of`组。多个候选项共同受“相关项目经验者优先”修饰时采用相同规则。

“如”“例如”“等”只表示举例时，示例本身不能自动成为独立要求或任选组。

## 5. 类别

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

Embedding和Vector Database与RAG相关，但类别是`retrieval`，不能全部压成`rag`。

## 6. 重要程度

- `must`：任职要求中的普通条件，以及明确使用“必须、要求、熟悉、掌握、精通”等措辞的条件；
- `preferred`：明确出现“优先、加分、更佳、有经验者优先”等措辞；
- `mentioned`：只在职责、场景或方向介绍中出现，没有要求候选人掌握；
- `unknown`：原文无法判断。

同一技能在基本要求中为`must`、在加分项中又出现更高阶经验时，保留能表达差异的两项和两条证据，不能简单覆盖。

## 7. 熟练度

| JD措辞 | proficiency |
|---|---|
| 了解、理解基础概念 | `understand` |
| 熟悉、能够使用 | `familiar` |
| 熟练、掌握、具备扎实能力 | `proficient` |
| 精通、专家级 | `expert` |
| 有经验、参与过或表述不明确 | `unknown` |

项目经验不能自动推断成熟练度。

## 8. 经验年限

| 原文 | min_years | max_years | years_text |
|---|---:|---:|---|
| 3年以上 | 3 | null | 3年以上 |
| 3～5年 | 3 | 5 | 3～5年 |
| 1年左右 | 1 | null | 1年左右 |
| 有项目经验 | null | null | null |
| 经验不限 | 0 | null | 经验不限 |

默认只把最低年限视为能力门槛。`max_years`只记录原文明示范围，不能自动解释为超过上限就不合格；只有原文明示排他限制时才用于筛选。

## 9. 禁止推断

- 不根据工资推断岗位级别；
- 不根据公司规模推断技术要求；
- 出现RAG时不自动补充Embedding和Vector Database；
- 出现Agent时不自动补充LangGraph；
- 出现后端开发时不自动补充FastAPI；
- 没有明确年限时不猜测年限；
- 不根据常识修改重要程度或熟练度；
- 抽取阶段不把JD原词替换为标准技能名。

证据和Golden规则见[DATASET_EVALUATION.md](DATASET_EVALUATION.md)。
