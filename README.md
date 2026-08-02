# JD Skill Insight

面向个人求职决策的岗位要求洞察系统。

项目从个人求职时难以横向比较大量JD的真实问题出发，通过抽取数据合同约束、要求原子化、跨JD原子要求归并、证据绑定和分层评测，把非结构化JD转化为可统计、可追溯的岗位要求数据。

## 当前状态

已实现 JD 导入、结构化抽取和跨 JD 要求归并的基础链路。
P0-3 两段式原子化候选正在完成正式验收裁决；
P0-4 分块输出与规则合同已达标，验收口径为规则 + 稳定性 + 人工抽样复核，149 实例正式验收待授权。
详细状态、指标和下一步见 [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) 与 [docs/work/](docs/work/)。

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

开发期实验和临时验收脚本位于`scripts/experiments/p0_x/`，并以`python -m ...`运行；真实外部调用使用`--execute`并选择数据库目标。目录和输出说明见[scripts/experiments/README.md](scripts/experiments/README.md)。

## 方法文档

- [全项目术语词典](docs/GLOSSARY.md)：核心业务术语与跨阶段不变量。
- [人工标注规范](docs/annotation/README.md)：按职责、要求和数据集评测三个主题提供规则入口。
- [项目路线图](docs/PROJECT_PLAN.md)：P0功能范围、硬依赖、验收输入和当前状态。
- [P0 工作文档](docs/work/)：每个已启动 P0 的目标、当前实现、验证、问题和结论。
- [项目决策](docs/DECISIONS.md)：有面试价值的技术决策与重大问题复盘。
- [实验报告](reports/README.md)：详细实验材料的分组与隐私边界。
