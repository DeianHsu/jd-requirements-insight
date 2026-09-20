# 架构

当前目标是让使用者把 Markdown JD 目录交给一个命令，得到可统计、可追溯原文证据的
MVP 市场要求报告。

## 使用者一键主线

```text
Markdown JD 目录
→ 输入目录指纹 → data/private/runs/<fingerprint>/
→ 导入隔离 SQLite 数据库
→ v0.10 + Schema V3 两段式抽取（有限纠错）
→ Schema / coverage / evidence existence / group contracts
→ auto-v1 系统策略批准 → 共用抽取正式化核心
→ 单次保守归并（有限纠错；不确定时 singleton）
→ exact ID coverage / unique mapping / structural contracts
→ auto-v1 系统策略批准 → 共用归并正式化核心
→ 独立 JD 统计
→ 原文 evidence 追溯
→ 确定性 Markdown 市场报告
```

抽取先收集整批结果，再在一个事务中正式化；任一 JD 失败时整批不写。归并失败时不产生
正式批次或报告。运行摘要记录 completed/failed、停止阶段和输入身份；同一输入完整成功后
直接复用。

## 开发者高保证验证链

```text
规则场景 / 真实 JD 多次 acceptance
→ 稳定性与变形分析
→ 人工规则或 cluster 审计
→ 文件型 finalize-extraction / finalize-consolidation
```

这条链用于开发者改变模型、Prompt、Schema 或规则后评估并校准自动边界，不是使用者每批
JD 的运行步骤。文件型 finalize 与 `analyze-jds` 复用同一正式化写入核心。

## 可选单次预检支线

```text
JD ─────────────────────────→ extract-jds --candidate-output
已定稿 requirement instances → consolidate-requirements --candidate-output
                               ↓
                         私有单次 candidate JSON
                         （快速检查后结束）
```

candidate 不进入一键主线或 acceptance，不作为文件型 finalize 输入，不写正式抽取/归并
表。它只用于开发者快速观察一次模型输出，可以完全跳过。

## 模块边界

| 模块 | 职责 |
|---|---|
| `app/auto_pipeline.py` | 私有指纹运行空间、一键编排、自动批准、失败摘要与完成结果复用 |
| `app/ingestion.py` | Markdown JD 导入、输入校验与内容哈希去重 |
| `app/extraction.py` / `app/extraction_two_stage.py` | 两段式抽取、证据校验与有限重试 |
| `app/extraction_validation.py` | 抽取合同、规则场景、变形与漂移检查 |
| `app/requirement_consolidation.py` / `app/consolidation.py` | 归并输入输出合同、单次聚类与确定性 mappings |
| `app/consolidation_validation.py` | 覆盖、结构、顺序变形与稳定性检查 |
| `app/candidates.py` | 可选单次 candidate JSON；不得写正式业务表 |
| `app/extraction_finalization.py` | 人工/系统策略批准结果共用的幂等、回读校验与原子定稿 |
| `app/consolidation_finalization.py` | 人工/系统策略批准结果共用的精确覆盖、身份绑定和定稿 |
| `app/finalization.py` | 正式结果共同门禁、身份审计与抽取来源状态分类 |
| `app/market_analysis.py` / `app/market_report.py` | 独立 JD 统计、证据追溯、报告门禁与 Markdown 渲染 |
| `app/cli.py` | 一键分析及显式数据库目标的开发者预检、定稿、审计、验证和报告入口 |
| `app/models.py` / `app/database.py` | 关系模型、数据库初始化与当前结构门禁 |

实验脚本只负责编排多次运行、稳定性分析和人工裁决材料；正式数据语义与写入门禁位于
`app/`。

## 关键设计

### 两段式抽取

发现段先对整份 JD 做 responsibility / requirement / mixed / excluded 分句归属，判断段
再对局部候选块进行原子化和字段判断。确定性覆盖合同保证每个分句恰好归属一次，并禁止
responsibility 块产出候选人要求。

### 原文 evidence

每条要求携带 JD 中连续出现的原文证据。自动门禁验证证据存在性；开发者 acceptance
抽查证据是否足以支持名称、importance、proficiency 和年限判断。报告可逐项回查来源
JD 与 evidence。

### requirement instance 与 canonical requirement 分层

抽取产生保留事实和证据的 requirement instance；归并只创建跨 JD 统计用的 canonical
requirement。每个实例必须且只能映射到一个 canonical，模型输出来源分区，代码确定性
生成 mappings。归并调用通过 Responses API 传入与本地 Pydantic 复验同源的
JSON Schema，未知字段、缺失字段、空来源与类型错误不是可兼容输出。
归并不得改写实例属性或证据。

### 身份与正式化

模型运行具有随机性，生成函数不能直接写正式表。一键主线以 `auto-v1` 记录输入范围、
模型、Prompt、Schema、策略版本、批准运行和结果指纹，通过机器硬门后调用共用正式化
核心；开发者高保证链则由 acceptance 与人工审核提供批准身份。两者都必须在身份和内容
完全一致时才幂等复用，失败不得留下部分正式数据。

自动硬门能够证明结构合法、覆盖完整、证据文本存在和映射一致，但不能证明每个语义判断
都正确。因此一键报告是 MVP 分析结果；人工语义审计被移到开发者评测周期，而不是伪装成
已经自动解决的问题。

报告入口还会回查归并定稿身份、mapping/partition、requirement → extraction → JD 链和
上游 provenance。缺少现行机器绑定的历史来源只有在私有、范围受限的结构化 waiver
明确覆盖时才能继续生成报告，风险提示仍写入报告；当前公开边界见
[CURRENT_STATE.md](CURRENT_STATE.md)。

### 显式数据库目标

一键入口按输入指纹确定私有隔离数据库，不读取或写入隐式项目数据库。其他数据库入口
必须显式选择项目数据库或数据库 URL；只读入口不会创建不存在的 SQLite 文件。P0-4
实验脚本只接受显式 `--database-url`，便于优先使用临时副本。

### 报告排序与呈现

频率榜按独立 JD 数降序、名称升序；准备优先级榜按必需 JD 数、加分 JD 数、总出现
JD 数依次降序，最后名称升序。报告仅展示中文 JD 级计数；任选关系从实例传递并标注，
原文证据和来源清单折叠在末尾。指纹、模型版本及批次身份保留在数据库和运行记录中。

### 统计口径

市场频率以独立 JD 数为主，同一 JD 内多个实例对同一 canonical 只贡献一次覆盖；实例数
只作补充。排序为 distinct_job_count 降序 → instance_count 降序 → canonical_name
升序。importance 同时保留实例级诊断口径与 JD 级报告口径。
