# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：修订剪枝后的全仓文档收缩方案（2026-08-15）

### 结论

复核 ChatGPT 的 5 点意见后，全部采纳，并对第 4 点作了更精确的定位：
ARCHITECTURE 正文已经说明单次 candidate 不进入正式定稿链路，但数据流图仍把 candidate
串在正式链上，造成图文语义不一致。实施时应重画成“正式 acceptance 链”和“可选单次
预检”两条分支，不能只补一句解释。

修订后的核心原则是：

1. v0.1 已完成并冻结，文档从“建设 MVP”切换为“维护已完成基线”；
2. 正式链只表达 acceptance 多次运行 → report/raw → human review → finalize；
3. `extract-jds --candidate-output` 与 `consolidate-requirements --candidate-output`
   是可选单次预检，不是 finalize 输入，也不是正式链的必经站；
4. PROJECT_PLAN 只保留两个影响正式结果解释的 accepted exceptions；
5. README 必须公开保留一句 JD1～3 历史 provenance 限制；
6. 状态型数字和一次性阶段过程不进入 GLOSSARY 或 ARCHITECTURE。

本轮只修订方案并覆盖本文件，不实施其他文档重写，不修改代码、Schema、正式数据或私有
artifact。

### 审核意见处理

| # | 核实结果 | 修订决定 |
|---|---|---|
| 1. AGENTS 生命周期与主线 | 成立。仍写“完成 MVP 为最高优先级”，主线也省略 acceptance/review/finalize | AGENTS 列为第一优先级重写 |
| 2. PROJECT_PLAN 例外数量 | 成立。`reviewed_unbound` 是展示命名债，不改变正式结果边界 | 正式 exception 只保留 provenance waiver 与归并稳定性 |
| 3. GLOSSARY 过期术语 | 成立。“正式JD样本范围 15～20”已失实；抽取器版本仍写“用于幂等保存” | 删除状态型术语，改正版本身份用途 |
| 4. ARCHITECTURE candidate 边界 | 成立。正文正确但数据流图把可选 candidate 串进正式链 | 数据流拆为正式链与可选预检支线 |
| 5. README provenance | 成立。README 现有风险提示应保留，而不是按原方案删除 | 压缩为一句并链接 CURRENT_STATE |

### 仍然有效的已确认问题

#### P1：P0-4 命令文档错误

README 和 CURRENT_STATE 当前写出的：

```text
python -m scripts.experiments.p0_4.run_acceptance --use-project-database --all --execute
```

不是可执行命令。当前脚本要求：

- 必填 `--database-url`；
- 必填私有 `--raw-output` 才能执行付费验收；
- 不传 `--job-ids` 即选择全部 JD；
- 不支持 `--use-project-database` 或 `--all`。

证据：`scripts/experiments/p0_4/run_acceptance.py:377-486`。现有
`tests/test_pipeline_e2e.py` 只断言错误字符串存在，也需同步改成真实参数合同。

#### P1：公开 sample 字段语义偏离当前规则

`scripts/make_sample_report.py:41-116` 将所有合成 requirement 的 category 固定为
`other`、proficiency 固定为 `basic`。这与 FIELD-01/FIELD-03 不完全一致，例如
“掌握常用编程语言”应为 `advanced`，经验和学历项也不应统一为 `basic`。

`examples/market-report-sample.md` 是生成产物，禁止直接手改。应独立修复生成器夹具，
再确定性重建 sample。

### 修订后的文档职责

| 文档 | 唯一职责 | 修改强度 |
|---|---|---|
| `AGENTS.md` | 已冻结 v0.1 的维护边界、正式主线、安全、验收、提交与每轮推送规则 | 重点重写开头与开发原则 |
| `README.md` | 公开产品说明、最短可运行路径、核心结果和诚实的高层限制 | 中等收缩 |
| `docs/ARCHITECTURE.md` | 稳定架构、正式链/预检支线、模块边界与设计理由 | 重点修正数据流，中等收缩正文 |
| `docs/CURRENT_STATE.md` | 当前正式数据快照、安全门、两个 accepted exceptions 和卫生性限制 | 中等收缩 |
| `docs/PROJECT_PLAN.md` | v0.1 已完成基线、两个 accepted exceptions、当前无实施阶段 | 重点重写 |
| `docs/GLOSSARY.md` | 稳定业务术语与流水线不变量，不保存样本状态 | 定向清理 |
| `docs/annotation/REQUIREMENTS.md` | REQ/GROUP/FIELD 规范合同 | 保持正文 |
| `docs/annotation/RESPONSIBILITIES.md` | RESP 规范合同 | 保持不变 |
| `docs/annotation/VALIDATION.md` | EVID/COVER 与抽取、归并验收合同 | 小幅整理 |
| `app/README.md` | 当前 app 模块导航 | 基本保持 |
| `scripts/README.md` | 剪枝后剩余脚本及各自真实安全参数 | 重写短表 |
| `examples/market-report-sample.md` | 生成器产生的公开报告形态 | 禁止手改，修夹具后再生 |
| `docs/REVIEW_LOG.md` | 最近一次任务摘要或 review/方案 | 每轮覆盖，不记录 push 状态 |

### 逐文件实施方案

#### 1. AGENTS.md：先切换项目生命周期

- 将“当前唯一主线”改成正式链：
  `JD 导入 → 抽取 acceptance（多次运行/合同检查）→ 人工审核 → finalize-extraction
  → 归并 acceptance/稳定性分析 → 人工裁决 → finalize-consolidation → 统计/证据/报告`；
- 在主线旁明确两个 candidate CLI 只是可选单次预检，输出不能作为 finalize 输入；
- 将“当前以完成 MVP 为最高优先级”改成：
  **v0.1 已完成并冻结；默认只维护当前闭环、修复明确缺陷和保持可复现性；未经授权不扩充
  样本、不新增功能、不进入下一阶段**；
- 保留最小实现、禁止旧兼容、安全隐私、付费门禁、生产验收和提交规则；
- 保留已经加入的“一次用户请求即一次来回”和 Review Log 不记录 push 状态规则。

#### 2. docs/PROJECT_PLAN.md：只保留两个实质例外

- 删除未使用的状态语义表、P0-6 的 3→5 JD 过程、P0-8 的 8→12→15 时间线、历史日期和
  批次指纹；
- 将 P0-1～P0-8 压成“v0.1 已完成基线”能力清单；
- 正式 accepted exceptions 只保留：
  1. JD1～3 抽取 provenance waiver；
  2. 归并 positive-pair Jaccard 稳定性限制；
- `reviewed_unbound` 命名债不再编号为 exception；移到 CURRENT_STATE 的非阻塞卫生项，
  修复后直接删除该项；
- “当前下一步”只写暂无获授权实施阶段；新 JD 不继承 waiver，必须走当前正式链。

#### 3. docs/GLOSSARY.md：删除状态与旧写入语义

- 整行删除“正式JD样本范围 = 15～20 个”；它是阶段状态，不是稳定术语，也不能简单改成
  15；
- 将“抽取器版本”改为：
  `model_name + prompt_version + schema_version` 构成的抽取身份，用于验收、定稿、
  下游输入选择与 provenance 绑定；不再写“用于幂等保存”；
- “跨阶段不变量”改成“流水线不变量”，删除 P0-1/P0-2/P0-4 前缀；
- 去除“独立 JD 计数”的 `P0-4` 反例措辞，保持业务语义与阶段编号解耦；
- P0-N 术语若 PROJECT_PLAN 重写后不再承担导航价值，则一起删除；否则仅保留脚本目录
  命名解释。

#### 4. docs/ARCHITECTURE.md：重画两条路径

正式链：

```text
JD 导入
→ 抽取 acceptance 多次运行与质量验证
→ report/raw + 人工审核
→ finalize-extraction
→ 归并 acceptance 多次运行、顺序变形与稳定性分析
→ report/raw + 人工裁决
→ finalize-consolidation
→ 独立 JD 统计 → 原文证据 → Markdown 报告
```

可选预检支线：

```text
JD / 已定稿 requirement instances
→ 单次模型 candidate
→ 私有 JSON，仅供快速检查
→ 不进入 acceptance，不作为 finalize 输入，不写正式表
```

同时：

- 保留两段式抽取、实例/canonical 分层、唯一映射、显式数据库和独立 JD 统计理由；
- 将具体 P0-7 waiver 细节替换为通用的上游 provenance 门禁，链接 CURRENT_STATE；
- 删除与 README 重复的产品非目标、与 AGENTS 重复的私有目录规则。

#### 5. README.md：公开但不过度展开

- 保留产品定位、核心结果、正式 Pipeline、公开 sample、主要 CLI 和高层限制；
- Pipeline 与 ARCHITECTURE 一致：正式链不把单次 candidate 当作中间输入；
- candidate 命令放在“可选单次预检”小节，正式验收/定稿单独说明；
- 修正 P0-4 命令，显式给 `--database-url`、`--raw-output`、`--execute`；
- 保留一句简洁且醒目的真实限制：
  **JD1～3 的正式抽取缺少现行机器可验证 provenance，仅在范围受限 waiver 下供当前
  MVP 报告消费；详见 CURRENT_STATE**；
- 不在首页复制批准日期、指纹或完整 waiver 结构。

#### 6. docs/CURRENT_STATE.md

- 聚焦 15 JD / 409 instances / 329 canonical、consolidation #5、安全门和当前边界；
- accepted exceptions 与 PROJECT_PLAN 对齐，只列 provenance waiver 和稳定性限制；
- `reviewed_unbound` 单列为“非阻塞卫生项”，不与 accepted exceptions 混排；
- 删除完整长命令表，改为正式入口、验收脚本、只读审计三组短索引；
- 不记录已经完成的扩样过程，不重复解释架构理由。

#### 7. scripts/README.md / app/README.md / annotation

- scripts/README 列出当前 5 个实验脚本和 `make_sample_report`：
  P0-3 真实验收可选项目数据库或 URL；P0-4 三个脚本只接受 `--database-url`；
  付费入口要求 `--execute` 与私有 raw 输出；
- app/README 继续作为轻量模块地图，只同步正式链/可选 candidate 的职责措辞；
- REQUIREMENTS、RESPONSIBILITIES 不为缩短而改写；
- VALIDATION 标题改为同时覆盖抽取与归并，保留 hard gate/warning、人工审核与非当前数据
  规则，减少重复入口说明。

#### 8. 公开 sample

- 让 `_SAMPLE_JOBS` 显式提供 category、importance、proficiency；
- 使编程语言、经验、学历、软技能和 RAG 样例符合当前 FIELD 合同；
- 重新生成 `examples/market-report-sample.md`，核对统计结果及第二次生成无 diff；
- 独立提交，不与纯文档重写混合。

### 实施顺序与提交边界

1. `docs(lifecycle)`：更新 AGENTS 生命周期、PROJECT_PLAN 和 GLOSSARY；
2. `docs(pipeline)`：重画 ARCHITECTURE，收缩 CURRENT_STATE/README，修正 P0-4 命令和
   文档合同测试，同步 app/scripts README；
3. `docs(validation)`：只做 annotation 的必要去重与标题/链接同步；
4. `fix(sample)`：修生成器字段并再生公开报告；
5. 最终验证全部 Markdown 链接、CLI 参数、sample 确定性 diff、全量 pytest 与 Ruff。

每个提交都应保持可独立验证；不得借文档重写改变 Schema、业务语义、正式数据库或私有
artifact。

### 本轮核实

- 5 点审核意见均已用当前文档和代码重新核实；
- AGENTS 生命周期表述、PROJECT_PLAN 第三个命名债、GLOSSARY 两处过期内容均确认存在；
- ARCHITECTURE 的问题是数据流图与正确正文不一致；
- README 当前已公开 provenance 风险，修订方案明确保留；
- `.venv\\Scripts\\python.exe -m pytest --basetemp .pytest-review-revision`：
  **336 passed**；
- `.venv\\Scripts\\python.exe -m ruff check app scripts tests`：通过；
- `uv run pytest` 因当前 Windows 环境的 uv trampoline 路径错误未启动，已用同一项目
  虚拟环境完成等价执行；
- 未调用付费模型，未写数据库，未实施方案中的其他文件修改。
