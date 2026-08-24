# app 目录说明

该目录保存应用的功能代码，当前主线为：
`analyze-jds` → 私有运行空间 → 抽取自动硬门与正式化 → 归并自动硬门与正式化 →
市场统计 → 证据追溯 → Markdown 报告。acceptance / 人工 review / 文件型 finalize
是开发者离线验证工具，不是使用者主线。

| 文件 | 职责 |
|---|---|
| `cli.py` | 一键分析，以及显式数据库目标的开发者候选、定稿、审计、验证与报告 CLI |
| `auto_pipeline.py` | 私有指纹运行空间中的一键编排、自动批准、失败摘要与结果复用 |
| `config.py` | 从环境变量或 `.env` 读取并校验 LLM 配置 |
| `candidates.py` | 生成可选的抽取/归并单次预检 JSON；不作为 finalize 输入，不写正式业务表 |
| `ingestion.py` | 解析 Markdown + front matter，按内容哈希去重并逐文件事务导入 |
| `schemas.py` | Pydantic 定义 JD 输入与抽取数据合同（Schema V3 三级熟练度） |
| `extraction.py` | 结构化抽取、证据校验与有限重试；不写正式业务表 |
| `extraction_two_stage.py` | 两段式抽取（发现段 + 判断段）与中间合同 |
| `extraction_validation.py` | 抽取合同检查、锚点化变形比较、规则场景属性检查 |
| `extraction_finalization.py` | 人工或系统策略已批准抽取的身份校验、幂等保护和原子定稿 |
| `evaluation.py` | 确定性名称相似度工具（供抽取验证的 diagnostic 使用） |
| `requirement_consolidation.py` | 跨 JD 归并与映射的输入/输出合同、来源分区校验与确定性映射生成 |
| `consolidation.py` | 单次 LLM 聚类归并（canonical + 来源分区）、确定性 mappings 与定稿所需底层写入函数 |
| `consolidation_validation.py` | 归并合同校验、positive-pair Jaccard、漂移与验收报告 |
| `consolidation_finalization.py` | 人工或系统策略已批准归并的精确覆盖、身份绑定和原子定稿 |
| `finalization.py` | 正式结果共同门禁与离线来源/批次身份审计 |
| `market_analysis.py` | 市场统计：实例数、独立 JD 数、importance 分布、来源 JD 与证据、稳定排序 |
| `market_report.py` | 定稿门禁与确定性 Markdown 市场报告 |
| `models.py` | JD、抽取、要求、归并批次/标准项/映射的 ORM 模型 |
| `database.py` | SQLAlchemy Engine、Session、SQLite 外键与建表初始化 |
