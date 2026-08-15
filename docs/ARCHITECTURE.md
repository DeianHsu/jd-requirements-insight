# 架构

当前目标是把真实 JD 转化为可统计、可审核、可追溯原文证据的市场要求报告。

## 正式数据链

```text
JD 导入
→ v0.10 + Schema V3 抽取 acceptance
  （多次运行、合同检查、规则场景/真实 JD 验证）
→ report/raw + 人工审核
→ finalize-extraction 定稿 requirement instances
→ 归并 acceptance
  （多次运行、顺序变形、稳定性分析）
→ report/raw + 人工 must-link / cannot-link / 名称裁决
→ finalize-consolidation 定稿 canonical requirements 与唯一 mappings
→ 独立 JD 统计
→ 原文 evidence 追溯
→ 确定性 Markdown 市场报告
```

正式 finalize 只消费完整 acceptance 产物及其人工审核记录，不消费单次 candidate。

## 可选单次预检支线

```text
JD ─────────────────────────→ extract-jds --candidate-output
已定稿 requirement instances → consolidate-requirements --candidate-output
                               ↓
                         私有单次 candidate JSON
                         （快速检查后结束）
```

candidate 不进入 acceptance，不作为 finalize 输入，不写正式抽取/归并表。它只用于在发起
完整多次验收前快速观察一次模型输出，可以完全跳过。

## 模块边界

| 模块 | 职责 |
|---|---|
| `app/ingestion.py` | Markdown JD 导入、输入校验与内容哈希去重 |
| `app/extraction.py` / `app/extraction_two_stage.py` | 两段式抽取、证据校验与有限重试 |
| `app/extraction_validation.py` | 抽取合同、规则场景、变形与漂移检查 |
| `app/requirement_consolidation.py` / `app/consolidation.py` | 归并输入输出合同、单次聚类与确定性 mappings |
| `app/consolidation_validation.py` | 覆盖、结构、顺序变形与稳定性检查 |
| `app/candidates.py` | 可选单次 candidate JSON；不得写正式业务表 |
| `app/extraction_finalization.py` | 抽取 acceptance 身份、人工批准、指纹和原子定稿 |
| `app/consolidation_finalization.py` | 归并 acceptance、裁决绑定、精确覆盖和原子定稿 |
| `app/finalization.py` | 正式结果共同门禁、身份审计与抽取来源状态分类 |
| `app/market_analysis.py` / `app/market_report.py` | 独立 JD 统计、证据追溯、报告门禁与 Markdown 渲染 |
| `app/cli.py` | 显式数据库目标的预检、定稿、审计、验证和报告入口 |
| `app/models.py` / `app/database.py` | 关系模型、数据库初始化与当前结构门禁 |

实验脚本只负责编排多次运行、稳定性分析和人工裁决材料；正式数据语义与写入门禁位于
`app/`。

## 关键设计

### 两段式抽取

发现段先对整份 JD 做 responsibility / requirement / mixed / excluded 分句归属，判断段
再对局部候选块进行原子化和字段判断。确定性覆盖合同保证每个分句恰好归属一次，并禁止
responsibility 块产出候选人要求。

### 原文 evidence

每条要求携带 JD 中连续出现的原文证据。自动门禁验证证据存在性，人工审核验证证据是否
足以支持名称、importance、proficiency 和年限判断。报告可逐项回查来源 JD 与 evidence。

### requirement instance 与 canonical requirement 分层

抽取产生保留事实和证据的 requirement instance；归并只创建跨 JD 统计用的 canonical
requirement。每个实例必须且只能映射到一个 canonical，模型输出来源分区，代码确定性
生成 mappings。归并不得改写实例属性或证据。

### 身份与正式化

模型运行具有随机性，单次结构合法不等于正式结果。acceptance 记录输入范围、模型、
Prompt、Schema、运行和结果指纹；人工审核绑定批准运行；finalize 重新核对这些身份后才
原子写入模型生成的正式抽取或归并数据。重复 finalize 只有在身份和内容完全一致时才幂等
跳过，失败不得留下部分正式数据。

报告入口还会回查归并定稿身份、mapping/partition、requirement → extraction → JD 链和
上游 provenance。当前正式数据中的例外状态见 [CURRENT_STATE.md](CURRENT_STATE.md)，
关闭决策见 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

### 显式数据库目标

所有数据库入口必须显式选择项目数据库或数据库 URL，不能依赖隐式默认值。只读入口不会
创建不存在的 SQLite 文件；P0-4 实验脚本只接受显式 `--database-url`，便于优先使用
临时副本。

### 统计口径

市场频率以独立 JD 数为主，同一 JD 内多个实例对同一 canonical 只贡献一次覆盖；实例数
只作补充。排序为 distinct_job_count 降序 → instance_count 降序 → canonical_name
升序。importance 同时保留实例级诊断口径与 JD 级报告口径。
