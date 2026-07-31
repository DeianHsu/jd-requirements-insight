# tests 目录说明

该目录保存自动化测试，用临时文件和临时SQLite数据库验证功能，不修改真实项目数据。

## 文件职责

| 文件 | 覆盖范围 |
|---|---|
| `test_health.py` | FastAPI元数据与`/health`。 |
| `test_ingestion.py` | JD解析、内容哈希、幂等导入和文件级错误隔离。 |
| `test_cli.py` | CLI入口、N/A显示、错误摘要和抽取范围选项。 |
| `test_extraction.py` | Prompt边界、抽取数据合同、证据、重试、实验入口和抽取器版本幂等性。 |
| `test_evaluation.py` | 人工标准答案校验、名称代理指标和困难样例分层指标。 |
| `test_schemas.py` | 抽取数据合同V2的逻辑组、年限及旧字段兼容。 |
| `test_database.py` | SQLite数据库结构V1到V2增量迁移。 |
| `test_requirement_consolidation.py` | P0-4开发草稿的跨JD原子要求归并合同约束；目标合同尚未稳定。 |

## 测试原则

- 每个测试只验证一个清晰行为；
- 数据库测试使用 `tmp_path` 隔离，避免污染 `data/jd_skill_insight.db`；
- 修复缺陷时应先增加能够复现问题的测试；
- 新增功能代码文件时，应同步增加或更新对应测试，并维护本README。
