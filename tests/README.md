# tests 目录说明

该目录保存自动化测试，用临时文件和临时SQLite数据库验证功能，不修改真实项目数据。

## 文件职责

| 文件 | 覆盖范围 |
|---|---|
| `test_health.py` | FastAPI元数据与`/health`。 |
| `test_ingestion.py` | JD解析、内容哈希、幂等导入和文件级错误隔离。 |
| `test_cli.py` | CLI入口、N/A显示、错误摘要、抽取范围选项和指定归并批次离线评测。 |
| `test_extraction.py` | Prompt边界、抽取数据合同、证据、重试、实验入口和抽取器版本幂等性。 |
| `test_evaluation.py` | 人工标准答案校验、名称代理指标和困难样例分层指标。 |
| `test_schemas.py` | 抽取数据合同V2的逻辑组、年限及旧字段兼容。 |
| `test_database.py` | SQLite数据库结构V1到V2增量迁移。 |
| `test_requirement_consolidation.py` | P0-4跨JD原子要求归并合同、映射覆盖与关系一致性约束。 |
| `test_consolidation.py` | P0-4输入装配：多JD字段无损、抽取版本选择、范围完整性与输入身份。 |
| `test_consolidation_client.py` | 归并LLM客户端、Prompt v1领域无关性、解析与重试闭环。 |
| `test_consolidation_run.py` | 批量归并执行：装配、模型调用、失败隔离与汇总摘要。 |
| `test_consolidation_persist.py` | 归并持久化：幂等跳过、字段无损、外键追溯与版本/范围共存。 |
| `test_consolidation_evaluation.py` | 归并评测：映射准确率、关系Precision/Recall/F1和未映射状态。 |
| `test_extraction_two_stage.py` | P0-3两段式实验的中间合同、唯一覆盖、证据、重试行为、正式版本号（v0.7）与历史 Prompt 锁定。 |
| `test_experiment_scripts.py` | 实验脚本的导入安全、私有输出路径和真实调用显式确认（含 run_acceptance 默认不调用外部模型）。 |
| `test_extraction_validation.py` | P0-3 新协议确定性验证：合同检查（覆盖/证据/逻辑组/归属/身份）、运行间比较与场景属性检查。 |
| `test_extraction_metamorphic.py` | 规则场景文件结构、领域中性约束与确定性变换（`data/rule_scenarios/`）。 |
