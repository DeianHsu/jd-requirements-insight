# JD Skill Insight GitHub公开规则

本文规定公开仓库的文件范围、隐私边界和发布前检查，不规定日常代码开发或文档写作方式。

## 1. 公开仓库保留内容

- `AGENTS.md`：公开的Codex开发行为与贡献约束；
- `app/`：应用源代码及目录说明；
- `tests/`：自动化测试及目录说明；
- `docs/annotation/`、`docs/handoff/`、`docs/DOCUMENT_RULES.md`、`docs/PUBLISH_RULES.md`以及其他适合公开的架构、方法和演示文档；
- `data/raw_jds/.gitkeep`和`data/golden/jd_extractions/.gitkeep`：仅保留空目录；
- `pyproject.toml`、`uv.lock`、`.python-version`：环境和依赖声明；
- `.env.example`、`.gitignore`、根目录`README.md`：公开配置模板、忽略规则和项目说明。

根目录`README.md`只保留项目定位、当前能力、环境配置和使用方法，不记录内部开发计划或提交操作说明，也不得链接公开仓库中不存在的私有文档。

## 2. 禁止上传内容

- `docs/CONTEXT_ROUTING.md`、`docs/PROJECT_PLAN.md`和`docs/DECISIONS.md`等本地开发管理信息；
- `reports/`中的本地阶段实验报告；
- `.env`、API密钥、访问令牌和其他本地凭据；
- `.venv/`、uv缓存、Python缓存、测试缓存和构建产物；
- 本地SQLite数据库；
- `data/raw_jds/`中的真实JD正文；
- `data/golden/jd_extractions/`中的真实Golden标注；
- 真实简历、个人求职材料和`data/private/`中的内容。

`AGENTS.md`默认公开；只有以后加入私人信息、凭据或本机敏感配置时，才重新评估是否忽略。禁止把私人信息加入公开规则文件。

handoff默认提交到公开仓库，只能记录工程摘要、文件路径、case ID和聚合指标；不得包含真实JD正文、完整模型输出、Golden内容、密钥、本机绝对路径或个人材料。

## 3. 发布前检查

1. 检查`git status`和待提交差异，只提交本次预期文件。
2. 使用`.gitignore`和`git check-ignore`确认私有数据、数据库、报告、缓存和凭据未进入跟踪范围。
3. 搜索API密钥、令牌、绝对本机路径、真实简历和JD正文等敏感信息。
4. 确认公开README和文档链接不指向被忽略文件。
5. 确认需要保留的空数据目录包含`.gitkeep`，目录内没有真实数据。
6. 发布功能代码前确认必要测试和Ruff检查已通过；纯文档发布执行内容、链接、格式和敏感信息检查。
7. 确认handoff与实际Git差异、提交历史和测试结果一致，且没有复制被忽略文件的内容。

## 4. 提交与推送权限

1. Codex只检查差异并提供建议提交文件、Summary和Description，不执行commit。
2. commit和push均由用户手动完成；完成本地阶段交接不代表已经同步到GitHub。
