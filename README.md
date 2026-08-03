# JD Skill Insight

面向个人求职决策的岗位要求洞察系统。

项目从个人求职时难以横向比较大量JD的真实问题出发，通过抽取数据合同约束、要求原子化、跨JD原子要求归并、证据绑定和分层评测，把非结构化JD转化为可统计、可追溯的岗位要求数据。

## 当前状态

已实现 JD 导入、结构化抽取和跨 JD 要求归并的基础链路。
抽取验收协议已迁移为规则化验证（DEC-015）：数据合同 + 规则 ID + 稳定性 +
规则场景变形测试 + 人工违规审计；旧人工标准答案与 F1 指标降级为 legacy
历史材料。
当前唯一抽取配置：v0.8 + Schema V3（两段式，proficiency =
unknown/basic/advanced）。旧 Prompt、旧 Schema V2 与五级熟练度不再维护，
历史由 Git 与已有报告保存；旧数据明确拒绝并要求重新抽取。
P0-3 验收流程已具备（Track A 合成场景 / Track B 真实 JD，pilot 与
acceptance 阶段分层，人工规则审计），真实 Pilot / Acceptance 待执行；
P0-4A 当前实现可用（等待 v0.8 新输入重新验收），P0-4B 为实验功能不阻塞
主线。
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

## 结构化抽取

在 `.env` 中配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 后执行（当前唯一抽取配置：v0.8 + Schema V3）：

```powershell
# 开发默认最多抽取3份JD，避免一次修改触发全量调用
python -m app.cli extract-jds

# 针对性调试可指定一个或多个JD；正式回归才显式抽取全部JD
python -m app.cli extract-jds --job-id 1 --job-id 3
python -m app.cli extract-jds --all

python -m app.cli list-extractions
```

相同JD使用相同抽取器版本（模型、Prompt和抽取数据合同版本）重复抽取时会自动跳过，避免重复结果污染数据库。
旧 Schema（非 V3）数据会被明确拒绝并要求用 v0.8 重新抽取，不会混入归并与统计。

## 跨JD要求归并与离线验证

真实归并必须显式选择范围和覆盖该范围的抽取器版本；该命令会调用配置的LLM并产生费用：

```powershell
python -m app.cli consolidate-requirements --all `
  --extractor-version <模型|Prompt|抽取数据合同版本>
python -m app.cli list-consolidations
```

归并成功后，使用明确的批次ID离线验证（P0-4A 合同、P0-4B 关系图稀疏度与下游事实投影），不会再次调用LLM，也不会隐式选择最新批次：

```powershell
python -m app.cli validate-consolidation --consolidation-id <批次ID>
```

## 实验性脚本

开发期实验和临时验收脚本位于`scripts/experiments/p0_x/`，并以`python -m ...`运行；真实外部调用使用`--execute`（预检使用`--dry-run`）。目录和输出说明见[scripts/experiments/README.md](scripts/experiments/README.md)。
P0-3 新协议验收为双轨道：Track A 合成规则场景 `scripts/experiments/p0_3/run_acceptance.py`；Track B 真实 JD `scripts/experiments/p0_3/run_real_jd_acceptance.py`（均为 v0.8 + Schema V3）。`--phase pilot` 检查流程、收集指标；`--phase acceptance`（已冻结规则/范围/阈值）才可用于批准当前版本。

## 方法文档

- [全项目术语词典](docs/GLOSSARY.md)：核心业务术语与跨阶段不变量。
- [语义决策规则](docs/annotation/README.md)：按职责、要求和证据/场景协议三个主题提供规则入口（规则带稳定 ID）。
- [项目路线图](docs/PROJECT_PLAN.md)：P0功能范围、硬依赖、验收输入和当前状态。
- [P0 工作文档](docs/work/)：每个已启动 P0 的目标、当前实现、验证、问题和结论。
- [项目决策](docs/DECISIONS.md)：有面试价值的技术决策与重大问题复盘。
- [实验报告](reports/README.md)：详细实验材料的分组与隐私边界。
