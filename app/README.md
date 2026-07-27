# app 目录说明

该目录保存应用的功能代码，当前实现了FastAPI健康检查、JD文件校验、SQLite持久化、LLM结构化抽取、证据校验、Golden Dataset评测和命令行操作。

## 文件职责

| 文件 | 功能 | 实现原理 |
|---|---|---|
| `__init__.py` | 标记 `app` 为Python应用包 | 使项目可以通过 `python -m app.xxx` 方式运行模块 |
| `main.py` | 提供FastAPI应用和环境验证入口 | 创建FastAPI实例并暴露 `/health` 健康检查接口 |
| `database.py` | 管理数据库地址、Engine、Session和建表过程 | 使用SQLAlchemy连接本地SQLite，并通过Session工厂隔离每次数据库操作 |
| `models.py` | 定义JD原文、抽取任务、职责和要求等数据库表 | 使用SQLAlchemy ORM建立一对多关联，并以抽取版本联合唯一约束保证幂等性 |
| `schemas.py` | 校验JD导入和结构化抽取数据 | 使用Pydantic模型、枚举和范围约束拒绝缺失字段、非法分类与异常置信度 |
| `ingestion.py` | 解析、校验、去重并导入JD | 读取YAML Front Matter和正文，计算规范化正文的SHA-256哈希，再按文件独立事务写入数据库 |
| `config.py` | 读取并校验LLM运行配置 | 使用Pydantic Settings从环境变量或 `.env` 读取模型地址、名称和密钥 |
| `extraction.py` | 抽取JD职责和要求并保存原文证据 | 约束LLM返回JSON，经Pydantic及原文包含校验后有限重试，再按版本幂等写入SQLite |
| `evaluation.py` | 校验Golden Dataset并计算抽取指标 | 将模型结果与人工标注按要求名称匹配，计算精确率、召回率、F1和重要性准确率 |
| `cli.py` | 提供本地JD导入、抽取、查看和评测命令 | 使用Typer组织业务入口，并通过Rich展示批处理统计、错误原因和评测指标 |

## 当前数据流

```text
Markdown JD
  → Front Matter解析
  → Pydantic校验
  → 正文规范化与SHA-256哈希
  → 数据库重复检查
  → SQLAlchemy写入SQLite
  → CLI展示结果
```

## 结构化抽取数据流

```text
数据库中的JD原文
  → 拼接JSON Schema与抽取规则
  → 兼容OpenAI协议的LLM返回JSON
  → Pydantic结构校验
  → 逐条核对原文证据
  → 失败时携带错误原因有限重试
  → 按模型、Prompt和Schema版本幂等写入SQLite
  → 与人工Golden Dataset对比评测
```

## 维护要求

新增或修改本目录代码文件时，必须同步更新上面的文件职责和数据流说明。
