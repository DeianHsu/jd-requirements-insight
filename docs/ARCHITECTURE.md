# 架构

当前唯一目标：**把真实 JD 转化为可统计、可追溯的市场要求报告**。

## 数据流

```text
JD 导入
→ v0.8 + Schema V3 结构化抽取（两段式：发现段 + 判断段）
→ 抽取质量验证（P0-3A 规则场景 / P0-3B 真实 JD）
→ requirement instance 归并为 canonical requirement（唯一映射）
→ 独立 JD 统计（app/market_analysis.py）
→ 原文证据追溯
→ Markdown 市场分析报告
```

## 模块

| 模块 | 职责 |
|---|---|
| `app/ingestion.py` | Markdown JD 导入与去重 |
| `app/extraction.py` / `app/extraction_two_stage.py` | v0.8 两段式抽取（发现段全局扫描、判断段局部判断）、证据校验、有限重试 |
| `app/extraction_validation.py` | 抽取合同检查、锚点化变形比较、规则场景属性检查 |
| `app/requirement_consolidation.py` | 归并输入/输出合同与确定性一致性校验 |
| `app/consolidation.py` | 分阶段归并（标准项轮 + 映射轮）、幂等持久化 |
| `app/consolidation_validation.py` | 归并合同校验、positive-pair Jaccard、canonical/singleton 漂移、验收报告 |
| `app/market_analysis.py` | 市场统计：实例数、独立 JD 数、importance 分布、来源 JD 集合、原始 requirement/evidence、稳定排序 |
| `app/cli.py` | 本地 CLI（import-jds / extract-jds / consolidate-requirements / list-* / validate-consolidation） |
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

当前合同要求每个 requirement instance 恰好映射到一个 canonical
requirement（不确定时创建 singleton）。唯一映射使统计口径确定：
每个实例计数一次，每份 JD 对一个 canonical 只计一次独立 JD 数。

### 为什么当前不引入 Agent、RAG、Web 服务

本项目是本地 CLI 数据分析闭环：导入 → 抽取 → 归并 → 统计 → 报告。
不建设 Web 服务、不引入 Agent 编排（LangGraph）、不引入 RAG 检索，
避免无关复杂度。当前唯一的模型调用是抽取与归并的付费 LLM 调用，必须
显式确认。

### 隐私与数据边界

真实 JD、人工标注、数据库、密钥与原始模型响应属于私有材料，只存在于
`data/private/`、`data/raw_jds/` 与本地数据库，不提交 Git。验收/报告
输出只含统计与脱敏索引。
