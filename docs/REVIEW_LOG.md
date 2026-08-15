# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：剪枝后的全仓文档收缩分析（2026-08-15）

### 本轮范围与结论

完整检查了 13 个受版本控制的 Markdown 文件，并对照当前文件树、CLI/脚本参数和文档
合同测试核实。未发现失效的本地 Markdown 链接，也不建议删除任何现有受版本控制文档；
下一轮应做的是明确单一事实来源、删除重复状态和阶段历史、修正两处已确认的内容错误。

本轮只形成方案，没有实施下述文档重写。实际修改仅限：

- 在 `AGENTS.md` 明确“一次对话”是一次用户请求与助手完成任务的一次来回，每次来回
  完成时最多统一 push 一次；
- 明确 Review Log 不记录是否已推送，远程状态由 Git/GitHub 表达；
- 覆盖更新本文件。

本地忽略的 Golden、raw JD、私有报告等历史 artifact 不属于公开文档收缩范围。

### 已确认问题

#### P1：命令文档与正式脚本不一致

`README.md` 和 `docs/CURRENT_STATE.md` 当前示例：

```text
python -m scripts.experiments.p0_4.run_acceptance --use-project-database --all --execute
```

不是可执行合同。当前 P0-4 验收脚本只接受必填 `--database-url`，全部 JD 是不传
`--job-ids` 时的默认行为，并且付费执行还必须显式传入 `--raw-output`
（`scripts/experiments/p0_4/run_acceptance.py:377-486`）。现有
`tests/test_pipeline_e2e.py` 只做字符串断言，因此把错误示例当成了“文档合同”。

修改方案：

1. README 改成带占位数据库 URL 和私有 raw 输出路径的真实命令；
2. CURRENT_STATE 不再复制完整长命令，只列入口和硬性参数，链接 README；
3. 文档测试改为核对实际必需参数，避免再次固化不存在的选项。

#### P1：公开 sample 的字段语义与当前规则不完全一致

`examples/market-report-sample.md` 是生成产物，不能直接手改。但其生成器
`scripts/make_sample_report.py:41-116` 将所有 requirement 的 category 固定为
`other`、proficiency 固定为 `basic`；例如“掌握常用编程语言”按当前 FIELD-03
应为 `advanced`，经验/学历项也不应统一为 `basic`。

修改方案：先让生成器夹具显式携带符合 Schema V3 的 category、importance 和
proficiency，再重新生成 sample 并用确定性 diff 验证。该项涉及生成器与测试，必须作为
独立小模块实施，不在纯文字替换中顺手修改。

#### P2：状态、计划与架构职责重叠

- README、CURRENT_STATE、PROJECT_PLAN 同时保存 15 JD 结果、例外和阶段关闭信息；
- PROJECT_PLAN 仍保存 P0-6 的 3→5 JD 过程、8→12→15 扩样过程、日期和历史指纹，
  “当前下一步”实际描述的也是已经完成的事项；
- ARCHITECTURE 混入 P0-7 特定 waiver、当前产品不做什么和隐私存放位置，这些分别应由
  CURRENT_STATE、README 与 AGENTS 负责；
- GLOSSARY 的永久不变量仍以 P0-1/P0-2/P0-4 阶段编号表达，容易让业务合同依赖历史计划。

这些内容当前大多不矛盾，但重复维护会造成下一次状态变化时漂移。

### 文档职责重划

| 文档 | 唯一职责 | 修改强度 |
|---|---|---|
| `AGENTS.md` | 仓库协作、安全、验收、提交与每轮推送规则 | 本轮已补两条规则；其余保持 |
| `README.md` | 面向公开读者的产品说明、最短可运行路径、公开 sample 与高层限制 | 中等收缩并修正 P0-4 命令 |
| `docs/ARCHITECTURE.md` | 稳定的数据流、模块边界、持久化与 provenance 设计理由 | 中等收缩 |
| `docs/CURRENT_STATE.md` | 当前正式数据快照、当前例外、安全门和边界 | 中等收缩 |
| `docs/PROJECT_PLAN.md` | 当前获授权目标、尚未完成项和真实下一步 | 重点重写 |
| `docs/GLOSSARY.md` | 容易漂移的业务术语与永久不变量 | 小幅去阶段化 |
| `docs/annotation/REQUIREMENTS.md` | REQ/GROUP/FIELD 规范性规则 | 保持正文 |
| `docs/annotation/RESPONSIBILITIES.md` | RESP 规范性规则 | 保持不变 |
| `docs/annotation/VALIDATION.md` | EVID/COVER 与抽取、归并验收合同 | 小幅整理 |
| `app/README.md` | 当前 app 模块导航 | 基本保持 |
| `scripts/README.md` | 剪枝后仍保留脚本的逐项导航和各自安全参数 | 重写短表 |
| `examples/market-report-sample.md` | 由生成器产生的公开报告形态 | 禁止手改，修夹具后再生 |
| `docs/REVIEW_LOG.md` | 最近一次任务摘要或获授权的 review/方案 | 每轮覆盖，不记录 push 状态 |

### 逐文件修改方案

1. **README.md**
   - 保留产品定位、MVP 核心数字、Pipeline、公开 sample 和主要 CLI；
   - 将详细批次例外、frozen-base 过程和阶段编号移到 CURRENT_STATE/VALIDATION；
   - 修正 P0-4 验收命令，避免把付费验收命令混在普通 `pytest/ruff` 代码块中；
   - 已知限制只保留样本代表性、LLM 随机性、隐私不可复算和非产品范围四类。

2. **docs/CURRENT_STATE.md**
   - 保留 15 JD / 409 instances / 329 canonical、consolidation #5、JD 1～3 waiver、
     Jaccard 限制和当前无授权下一阶段；
   - 删除与 README 重复的完整入口表，改为“正式入口 / 验收脚本 / 只读审计”三组短索引；
   - 不重复解释架构理由，不记录已经完成的扩样过程；
   - 保留 `updated_at`，只在状态事实变化时更新。

3. **docs/PROJECT_PLAN.md**
   - 删除未被当前使用的状态语义表和 P0-6/P0-8 扩样时间线；
   - 将 P0-1～P0-8 的关闭历史压成一段“当前 MVP 基线已完成”能力清单；
   - 只保留三个当前例外/待办，其中已豁免项用当前约束描述，不保存批准过程叙事；
   - “当前下一步”只写“暂无获授权实施阶段”以及新增 JD 必须走当前主线。

4. **docs/ARCHITECTURE.md**
   - 保留数据流、模块边界、两段式抽取、实例/canonical 分层、唯一映射、显式数据库、
     独立 JD 统计和候选—定稿隔离；
   - 将具体 P0-7 waiver 条件改为通用的“报告必须验证上游 provenance”，细节链接
     CURRENT_STATE；
   - 删除“当前不引入 Agent/RAG/Web”和私有目录重复说明，分别由 README/AGENTS 承担。

5. **docs/GLOSSARY.md**
   - 保留术语表；
   - 将“跨阶段不变量”改成“流水线不变量”，移除 P0-1/P0-2/P0-4 前缀；
   - 不复制枚举和值表，继续链接 Schema 与 annotation。

6. **docs/annotation/**
   - REQUIREMENTS 与 RESPONSIBILITIES 作为规范性合同，不做压缩性改写；
   - VALIDATION 标题调整为覆盖抽取与归并的验证协议；
   - 保留 hard gate/warning、证据支持性和人工审核边界，减少与 README 的入口说明重复；
   - 非当前数据处理规则继续保留。

7. **app/README.md / scripts/README.md**
   - app/README 保持轻量模块导航，只在模块职责变化时同步；
   - scripts/README 改为列出剪枝后实际剩余的 5 个实验脚本及 `make_sample_report`；
   - 分别注明：P0-3 真实验收可选项目数据库或 URL；P0-4 三个脚本只接受显式
     `--database-url`；付费入口需要 `--execute` 和私有 raw 输出。

8. **examples/market-report-sample.md**
   - 不直接编辑；
   - 修正 `scripts.make_sample_report` 的合成字段后重新生成；
   - 验证报告结构、统计数字、字段语义和再次生成无 diff。

### 实施顺序

1. 先修正 P0-4 命令、scripts 导航和对应文档合同测试；
2. 再确定单一事实来源，依次收缩 PROJECT_PLAN → CURRENT_STATE → README；
3. 收缩 ARCHITECTURE，去阶段化 GLOSSARY，轻调 VALIDATION；
4. 独立修复 sample 生成夹具并再生报告；
5. 最后检查全部本地链接、文档引用路径、CLI 参数，运行文档合同测试、全量 pytest、
   Ruff 和 sample 确定性 diff。

建议拆成三个实施提交：`docs(commands)` 修正文档命令合同、
`docs(project)` 收缩项目文档、`fix(sample)` 对齐公开样例字段语义。不得在文档收缩中
修改 Schema、业务语义、正式数据库或私有 artifact。

### 本轮核实

- 受版本控制 Markdown：13 个，均已阅读；
- 本地 Markdown 链接：无失效目标；
- 已对照当前 CLI、P0-3/P0-4 argparse 和文档合同测试；
- `.venv\\Scripts\\python.exe -m pytest --basetemp .pytest-doc-review`：
  **336 passed**；
- `.venv\\Scripts\\python.exe -m ruff check app scripts tests`：通过；
- `uv run pytest` 因当前 Windows 环境的 uv trampoline 路径错误未启动，已用同一项目
  虚拟环境完成等价执行；
- 未调用付费模型，未写数据库，未修改其他项目文档。
