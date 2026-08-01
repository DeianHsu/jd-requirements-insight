# app 目录说明

该目录保存应用的功能代码，当前实现了FastAPI健康检查、JD文件校验、SQLite持久化、LLM结构化抽取、证据校验、人工标准答案评测和命令行操作。

## 文件职责

| 文件 | 职责与实现重点 |
|---|---|
| `__init__.py` | 标记Python应用包。 |
| `main.py` | 创建FastAPI应用并提供`/health`健康检查。 |
| `database.py` | 管理SQLAlchemy Engine、Session、建表和可重复执行的SQLite增量迁移。 |
| `models.py` | 定义JD、抽取结果、职责、要求及跨JD归并（批次/标准要求项/映射/关系）的ORM关系与幂等约束。 |
| `schemas.py` | 用Pydantic定义JD输入和抽取数据合同V2。 |
| `ingestion.py` | 解析Markdown与Front Matter，按内容哈希去重并逐文件事务导入。 |
| `config.py` | 从环境变量或`.env`读取并校验LLM配置。 |
| `extraction.py` | 执行结构化抽取、抽取数据合同与证据校验、有限重试和按抽取器版本持久化。 |
| `evaluation.py` | 校验人工标准答案并计算完整JD及困难样例的分层评测指标。 |
| `requirement_consolidation.py` | P0-4开发草稿：定义跨JD原子要求归并与映射的输入、输出和确定性约束；合同尚未稳定。 |
| `consolidation.py` | P0-4归并执行：装配输入、LLM客户端与Prompt v1、解析与有限重试、批量执行与失败隔离、幂等持久化；归并合同见`requirement_consolidation.py`。 |
| `cli.py` | 用Typer提供导入、查看、抽取、归并和评测命令。 |

## 当前数据流

```text
Markdown JD
  → Front Matter解析
  → Pydantic校验
  → 内容哈希去重并写入SQLite
  → LLM结构化抽取
  → 抽取数据合同与证据校验及有限重试
  → 按抽取器版本幂等写入SQLite
  → 人工标准答案与困难样例分层评测
```

## 维护要求

新增或修改本目录代码文件时，必须同步更新上面的文件职责和数据流说明。
