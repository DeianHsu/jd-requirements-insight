# JD Skill Insight

面向个人求职决策的岗位要求洞察系统。

项目从个人求职时难以横向比较大量JD的真实问题出发，通过抽取数据合同约束、要求原子化、跨JD原子要求归并、证据绑定和分层评测，把非结构化JD转化为可统计、可追溯的岗位要求数据。

## 当前状态

已实现Markdown JD校验、SQLite导入、内容哈希去重、JD结构化抽取和跨JD原子要求归并的基础链路。当前抽取数据合同V2与正式Prompt V2.3.1已经完成5份真实JD抽取；P0-3的两段式抽取仍是未接入正式流程的归档实验。P0-4已经完成输入版本身份、语料范围完整性、SQLite外键、关系Precision/Recall/F1和关系图冲突的确定性修复，Prompt v1.7小规模真实验证达到预设指标，但连续3次正式完整验收均未形成批次，现已暂停修复，不能作为下游统计的稳定数据源。

## 环境

- Python 3.11+
- 使用 `uv` 管理虚拟环境和依赖

## 从 GitHub 克隆后配置环境

先安装 Git 和 `uv`，然后执行：

```powershell
git clone <你的仓库地址>
cd jd-skill-insight

# 根据 .python-version 和 uv.lock 创建 Python 环境并安装依赖
uv sync

# 创建本地配置；.env 已被 gitignore，不会上传
Copy-Item .env.example .env

# 激活项目环境
.\.venv\Scripts\Activate.ps1

# 验证项目
python -m app.main
python -m pytest
ruff check --no-cache app tests
```

如果不想激活环境，也可以直接执行：

```powershell
uv run python -m app.main
uv run pytest
uv run ruff check --no-cache app tests
```

## 导入JD

把Markdown格式的JD放入 `data/raw_jds/`，然后执行：

```powershell
python -m app.cli import-jds data/raw_jds
python -m app.cli list-jds
```

导入操作是幂等的：同一份JD重复执行时会按正文内容哈希跳过，不会重复写入数据库。

## 结构化抽取与评测

准备自己的JD和人工标注数据后，可以校验人工标准答案及其中的原文证据：

```powershell
python -m app.cli validate-golden data\golden\jd_extractions data\raw_jds
```

在 `.env` 中配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 后执行：

```powershell
# Prompt开发默认最多抽取3份JD，避免一次修改触发全量调用
python -m app.cli extract-jds

# 针对性调试可指定一个或多个JD；正式回归才显式抽取全部JD
python -m app.cli extract-jds --job-id 1 --job-id 3
python -m app.cli extract-jds --all

python -m app.cli list-extractions
python -m app.cli evaluate-extractions data\golden\jd_extractions

# 使用自己的困难样例文件比较指定抽取版本的分层指标
python -m app.cli evaluate-cases <annotation_cases.json> `
  --prompt-version 2.3.1 --schema-version 2.0 --model <模型名称> `
  --split development
```

相同JD使用相同抽取器版本（模型、Prompt和抽取数据合同版本）重复抽取时会自动跳过，避免重复结果污染数据库。
`evaluate-cases`默认只显示前10条错误摘要，可使用`--max-issues`调整；完整模型JSON保存在本地数据库中，不在终端默认输出。

## 跨JD要求归并与离线评测

真实归并必须显式选择范围和覆盖该范围的抽取器版本；该命令会调用配置的LLM并产生费用：

```powershell
python -m app.cli consolidate-requirements --all `
  --extractor-version <模型|Prompt|抽取数据合同版本>
python -m app.cli list-consolidations
```

归并成功后，使用明确的批次ID离线评测，不会再次调用LLM，也不会隐式选择最新批次：

```powershell
python -m app.cli evaluate-consolidation <consolidation_cases.json> `
  --consolidation-id <批次ID>
```

## 实验性脚本

开发期实验和临时验收脚本统一放在`scripts/experiments/p0_x/`，不得放在项目根目录。脚本以`python -m ...`运行；真实外部调用必须显式传入`--execute`并选择数据库目标。目录和输出规则见[scripts/experiments/README.md](scripts/experiments/README.md)。

## 方法文档

- [全项目术语词典](docs/GLOSSARY.md)：统一数据单位、抽取数据合同、评测、要求归并和公共CLI的固定含义。
- [人工标注规范](docs/annotation/README.md)：按职责、要求和数据集评测三个主题提供规则入口。
- [DeepSeek冷启动续开发指南](docs/DEEPSEEK_CONTINUATION.md)：在无历史聊天上下文时恢复Codex对P0-3/P0-4的审计补充、实验结论、后续步骤和验收边界。
