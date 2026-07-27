# tests 目录说明

该目录保存自动化测试，用临时文件和临时SQLite数据库验证功能，不修改真实项目数据。

## 文件职责

| 文件 | 功能 | 实现原理 |
|---|---|---|
| `test_health.py` | 验证应用名称和FastAPI健康检查接口 | 检查应用元数据，并使用TestClient调用 `/health` 验证HTTP状态码和JSON结果 |
| `test_ingestion.py` | 验证JD解析、哈希、导入和错误隔离 | 动态生成Markdown样本和临时数据库，覆盖正常导入、重复导入及错误文件场景 |
| `test_cli.py` | 验证命令行入口 | 使用Typer的CliRunner执行导入和列表命令，并检查用户可见输出 |
| `test_extraction.py` | 验证JD结构化抽取和持久化 | 使用假LLM客户端覆盖Schema校验、证据约束、重试和抽取版本幂等性 |
| `test_evaluation.py` | 验证黄金数据和评测指标 | 构造预测与标准答案，检查Precision、Recall、F1、重要程度准确率和证据有效性 |

## 测试原则

- 每个测试只验证一个清晰行为；
- 数据库测试使用 `tmp_path` 隔离，避免污染 `data/jd_skill_insight.db`；
- 修复缺陷时应先增加能够复现问题的测试；
- 新增功能代码文件时，应同步增加或更新对应测试，并维护本README。
