# P0-8 扩充真实JD

```yaml
p0_id: "P0-8"
plan_item: "扩充至15～20份真实JD"
status: "partial"
baseline_commit: "1f50451"
verified_revision: "working tree based on 1f50451"
related_decisions: ["DEC-003", "DEC-013"]
glossary_terms: ["原始JD", "结构化抽取", "人工标准答案"]
depends_on: ["P0-1", "P0-2", "P0-3", "P0-4"]
affects: ["P0-10", "P0-11"]
```

# 功能目标与边界

建立可重复的JD导入和抽取数据准备流程，并把目标范围扩充到足以支持市场统计的样本规模。P0-8只管理数据进入系统的质量和范围，不决定要求归并或统计口径。

# 当前状态

部分完成。Markdown JD解析、Front Matter校验、SHA-256正文哈希去重、逐文件事务导入和错误隔离已经实现；当前本地真实数据为5份，尚未达到15～20份目标。

# 稳定事实

- 同一JD重复导入时按规范化正文哈希跳过，不重复写入数据库。
- 单个无效文件不能阻断同批其他有效文件导入。
- 真实JD、由其产生的人工标准答案和数据库只保存在本地忽略目录。
- 扩充数据前应先稳定抽取数据合同、原子化和要求归并流程，避免昂贵返工。

# 实现与文件入口

- `app/ingestion.py`：解析、校验、哈希、导入和错误隔离。
- `app/cli.py`：`import-jds`和`list-jds`。
- `app/models.py`、`app/database.py`：JD持久化。
- `tests/test_ingestion.py`、`tests/test_cli.py`：导入和CLI测试。
- `docs/PUBLISH_RULES.md`：真实数据公开边界。

# 数据合同与不变量

- 输入是带YAML Front Matter的Markdown JD。
- 数据来源和筛选标准必须可记录，但不得在公开文档复制真实正文。
- 哈希去重基于正文内容，不依赖本机绝对路径。
- 测试使用临时虚构文件，不修改真实项目数据。

# 测试与验证

当前工作树43项测试通过，Ruff通过。导入测试覆盖元数据解析、空白规范化、幂等导入和文件级错误隔离。

# 未完成事项与已知问题

- 样本规模尚未达到15～20份。
- 尚缺完整的数据来源与筛选记录闭环。
- 批量真实抽取依赖P0-3和P0-4进一步稳定。

# 继续开发入口

数据导入缺陷从`app/ingestion.py`和对应测试开始。扩充真实样本时只读取目标文件并遵守私有数据边界，不默认扫描整个真实JD目录。
