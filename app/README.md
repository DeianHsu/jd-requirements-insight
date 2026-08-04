# app 目录说明

该目录保存应用的功能代码，当前主线为：
JD 导入 → v0.10 + Schema V3 结构化抽取 → 抽取质量验证 → 要求事实归并 →
市场统计 → 证据追溯 → Markdown 报告。

| 文件 | 职责 |
|---|---|
| `cli.py` | 本地 CLI（import-jds / extract-jds / consolidate-requirements / list-* / validate-consolidation） |
| `config.py` | 从环境变量或 `.env` 读取并校验 LLM 配置 |
| `ingestion.py` | 解析 Markdown + front matter，按内容哈希去重并逐文件事务导入 |
| `schemas.py` | Pydantic 定义 JD 输入与抽取数据合同（Schema V3 三级熟练度） |
| `extraction.py` | 结构化抽取、证据校验、有限重试、按抽取器版本幂等持久化 |
| `extraction_two_stage.py` | 两段式抽取（发现段 + 判断段）与中间合同 |
| `extraction_validation.py` | 抽取合同检查、锚点化变形比较、规则场景属性检查 |
| `evaluation.py` | 确定性名称相似度工具（供抽取验证的 diagnostic 使用） |
| `requirement_consolidation.py` | 跨 JD 归并与映射的输入/输出合同、来源分区校验与确定性映射生成 |
| `consolidation.py` | 单次 LLM 聚类归并（canonical + 来源分区）、确定性 mappings、幂等持久化 |
| `consolidation_validation.py` | 归并合同校验、positive-pair Jaccard、漂移与验收报告 |
| `market_analysis.py` | 市场统计：实例数、独立 JD 数、importance 分布、来源 JD 与证据、稳定排序 |
| `models.py` | JD、抽取、要求、归并批次/标准项/映射的 ORM 模型 |
| `database.py` | SQLAlchemy Engine、Session、SQLite 外键与建表初始化 |
