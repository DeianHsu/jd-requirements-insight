# 架构

当前唯一目标：**把真实 JD 转化为可统计、可追溯的市场要求报告**。

## 数据流

```text
JD 导入
→ v0.10 + Schema V3 抽取候选（私有 JSON，单次预检产物，不入正式表）
→ 抽取质量验证（P0-3A 规则场景 / P0-3B 真实 JD 完整验收：多次运行 +
  合同检查 + 人工审核；验收产物才是 finalize 的输入）
→ finalize-extraction 定稿正式 requirement instance
→ canonical requirement 归并候选（私有 JSON，单次预检产物）
→ 归并验收（P0-4）、稳定性分析与人工裁决
→ finalize-consolidation 定稿
→ 独立 JD 统计（app/market_analysis.py）
→ 原文证据追溯
→ Markdown 市场分析报告
```

## 模块

| 模块 | 职责 |
|---|---|
| `app/ingestion.py` | Markdown JD 导入与去重 |
| `app/extraction.py` / `app/extraction_two_stage.py` | v0.10 两段式抽取（发现段全局扫描、判断段局部判断）、证据校验、有限重试 |
| `app/extraction_validation.py` | 抽取合同检查、锚点化变形比较、规则场景属性检查 |
| `app/candidates.py` | 抽取/归并模型候选 JSON 生成；不得写正式业务表 |
| `app/extraction_finalization.py` | 抽取验收身份、审核绑定、指纹和原子定稿合同 |
| `app/requirement_consolidation.py` | 归并输入/输出合同与确定性一致性校验 |
| `app/consolidation.py` | 单次 LLM 聚类、确定性 mappings 与正式定稿所需的底层持久化能力 |
| `app/consolidation_validation.py` | 归并合同校验、positive-pair Jaccard、canonical/singleton 漂移、验收报告 |
| `app/consolidation_finalization.py` | 归并审核绑定、裁决指纹、精确覆盖和原子定稿合同 |
| `app/finalization.py` | 正式结果共同门禁、批次身份审计与抽取来源状态分类 |
| `app/market_analysis.py` | 市场统计：实例数、独立 JD 数、importance 双口径（实例级/JD 级）、来源 JD 集合、原始 requirement/evidence、稳定排序（独立 JD 数优先） |
| `app/market_report.py` | 只消费完成定稿的归并批次并确定性生成 Markdown 报告 |
| `app/cli.py` | 显式数据库目标的候选、定稿、审计、验证与报告入口 |
| `app/models.py` / `app/database.py` | ORM 模型与数据库初始化 |

## 架构理由

### 为什么使用两段式抽取

发现段先做全局分句归属（responsibility / requirement / mixed / excluded），
判断段只做局部语义判断。两段之间用确定性覆盖检查衔接（每个分句恰好归属
一个候选块），避免单次完整抽取中"全局扫描"与"字段判断"互相干扰，也让
"职责不得误抽成候选人要求"成为可检查的合同（responsibility 块产出
requirement 即违规）。

### 为什么 evidence 必须来自原文

每条要求（以及归并时每个标准项的依据）都携带连续原文证据。证据必须
逐字存在于 JD 原文中（`validate_evidence`），防止模型幻觉；市场报告中的
每个统计结论都可以追溯到具体 JD 句子。

### 为什么要求实例与 canonical requirement 分层

抽取产生**要求实例**（每份 JD 的原子要求，保留原文证据）；归并产生
**canonical requirement**（跨 JD 标准要求项）。实例层保留事实与追溯，
canonical 层支持跨 JD 统计。两者通过唯一映射连接。

### 为什么 SQL 负责精确统计

归并批次以关系型结构持久化（canonical + mapping + 来源 requirement）。
市场统计直接读取持久化批次计算独立 JD 数等精确计数，结果可复现、可审计，
不依赖模型输出顺序或临时 ID。

### 为什么每个实例必须唯一映射

归并模型一次输出 canonical requirements 和来源实例分区
（`source_requirement_ids`，无法合并的实例创建 singleton）；分区校验
通过后，mappings 由确定性代码从来源分区生成并持久化。模型只负责决定
cluster，确定性代码负责把 cluster 展开为 mappings。归并持久化同时保存
成功那次的模型响应（`model_response`）与规范化结果
（`normalized_result`，含确定性 mappings）及尝试次数。离线验证
（validate-consolidation）按批次记录的 extraction_ids 回查原始输入集合
作为 coverage 分母，不用已有 mappings 自证。唯一映射使统计口径确定：
每个实例计数一次，每份 JD 对一个 canonical 只计一次独立 JD 数。

### 为什么候选使用私有文件、正式结果使用数据库

抽取与归并模型运行具有随机性，单次结构合法不等于已经通过稳定性和人工
审核。模型入口只写显式私有 JSON 候选（单次预检产物，仅供快速人工参考，
不进入正式定稿链路）；`finalize-extraction` 与
`finalize-consolidation` 核对**完整验收产物**（多次运行、合同检查、人工
审核）的输入、运行、审核和结果指纹后，才允许原子写入正式业务表。这样
无需候选状态机，正式表仍保持“可统计、可报告”的单一语义。
报告门禁要求归并批次具有审核决定指纹和来源运行标识，结构合法但未定稿的
候选不能成为市场结论。

### 为什么数据库目标必须显式选择

所有 CLI 数据库操作必须选择 `--database-url` 或
`--use-project-database`，二者不能同时使用。只读入口不会创建不存在的
SQLite 文件；实验与正式数据库因此不会因环境变量或默认路径被静默混用。

### 市场频率口径

市场高频以**独立 JD 数**为准（覆盖多少份 JD），不是 requirement instance
数；实例数只作补充信息。排序为 distinct_job_count 降序 → instance_count
降序 → canonical_name 升序。importance 分布提供两套口径：实例级（诊断
抽取与映射分布）与 JD 级（市场报告默认展示，同一 JD 按
must > preferred > mentioned > unknown 优先级只贡献一次，总数不超过
独立 JD 数）。

### 为什么当前不引入 Agent、RAG、Web 服务

本项目是本地 CLI 数据分析闭环：导入 → 抽取 → 归并 → 统计 → 报告。
不建设 Web 服务、不引入 Agent 编排（LangGraph）、不引入 RAG 检索，
避免无关复杂度。当前唯一的模型调用是抽取与归并的付费 LLM 调用，必须
显式确认。

### 隐私与数据边界

真实 JD、人工标注、数据库、密钥与原始模型响应属于私有材料，只存在于
`data/private/`、`data/raw_jds/` 与本地数据库，不提交 Git。验收/报告
输出只含统计与脱敏索引。
