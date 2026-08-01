# P0-9 核心自动化测试

```yaml
p0_id: "P0-9"
plan_item: "核心自动化测试"
status: "partial"
baseline_commit: "e1a89f1"
verified_revision: "development fixes based on e1a89f1"
related_decisions: ["DEC-004", "DEC-012"]
glossary_terms: ["确定性校验", "小规模验证", "全量回归"]
depends_on: []
affects: ["P0-11"]
dependency_mode: "task_scoped"
```

# 功能目标与边界

为P0核心规则提供与风险相匹配的确定性测试，并以完整测试和Ruff作为功能验收门槛。P0-9是横跨其他P0功能点的工程保障，不替代真实业务评测。

# 当前状态

部分完成。当前工作树共有110项测试并全部通过，Ruff通过。P0-4已覆盖输入变化、范围完整性、输入指纹、旧库迁移、SQLite外键、调用层重试、显式超时、关系假阳性、关系图冲突和指定批次离线评测；P0-3两段式实验已覆盖唯一分句归属及实验脚本安全边界。统计和统计结论证据查询尚无专项测试。

# 稳定事实

- 测试目录使用临时文件和临时SQLite数据库，不修改真实项目数据。
- 每个测试只验证一个清晰行为；缺陷修复应先增加可复现测试。
- 确定性测试用于结构、合同、迁移和聚合规则；LLM业务语义仍需人工标准答案评测。
- 新增、删除或重命名测试文件时同步维护`tests/README.md`。

# 实现与文件入口

- `tests/README.md`：测试文件职责索引。
- `tests/test_ingestion.py`：导入。
- `tests/test_schemas.py`、`tests/test_database.py`：抽取数据合同、数据库结构、SQLite外键和旧批次迁移。
- `tests/test_extraction.py`：Prompt、抽取、证据、重试和持久化。
- `tests/test_evaluation.py`：人工标准答案和评测。
- `tests/test_requirement_consolidation.py`及`test_consolidation*.py`：P0-4合同、输入选择、执行、持久化与评测。
- `tests/test_extraction_two_stage.py`：P0-3两段式实验中间合同和唯一覆盖。
- `tests/test_experiment_scripts.py`：实验脚本私有输出边界、导入安全和真实调用显式确认。
- `tests/test_cli.py`、`tests/test_health.py`：公共入口。

# 数据合同与不变量

- 测试不得调用有费用的外部服务。
- 不读取真实JD、完整标注、真实数据库或密钥。
- 测试数量本身不是验收目标；必须覆盖计划中的关键失败路径。

# 测试与验证

验证命令：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check --no-cache app tests
```

当前结果：110项测试通过，Ruff通过；存在一个来自FastAPI TestClient依赖链的Starlette弃用警告，不影响现有行为。

# 未完成事项与已知问题

- P0-6独立JD计数测试尚未建立。
- P0-7统计结论到证据的端到端查询测试尚未建立。
- P0-10报告生成测试尚未建立。

# 继续开发入口

修改任一P0功能时先运行目标测试，再运行完整测试和Ruff；同时更新本handoff中的总体验证结果及对应功能handoff中的专项结果。
