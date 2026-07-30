# JD Skill Insight

面向个人求职决策的岗位技能洞察系统。

项目从个人求职时难以横向比较大量JD的真实问题出发，通过Schema约束、要求原子化、技能本体、证据绑定和分层评测，把非结构化JD转化为可统计、可追溯的岗位技能数据。

## 当前状态

已实现 Markdown JD 的校验、SQLite 导入、内容哈希去重和列表查看，并完成JD结构化抽取基础设施：已经使用DeepSeek V4 Flash完成5份真实JD的多版本抽取，保存岗位方向、级别、职责、要求和原文证据，并可用人工Golden Dataset评测。当前Schema V2和Prompt V2.3.1能够表达职责业务边界、要求示例、具体技术名、任选逻辑组与经验范围；困难样例分层Evaluation可以按development、regression、validation、模型、Prompt和Schema版本报告原子项、字段、逻辑组、年限和证据指标，当前正在评估单次混合抽取中的跨任务干扰。

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

准备自己的JD和人工标注数据后，可以校验Golden Dataset及其中的原文证据：

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

相同JD使用相同模型、Prompt版本和Schema版本重复抽取时会自动跳过，避免重复结果污染数据库。
`evaluate-cases`默认只显示前10条错误摘要，可使用`--max-issues`调整；完整模型JSON保存在本地数据库中，不在终端默认输出。

## 方法文档

- [人工标注规范](docs/annotation/README.md)：按职责、要求和数据集评测三个主题提供规则入口。
