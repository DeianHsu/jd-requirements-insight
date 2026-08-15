# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：仓库剪枝分析（2026-08-15）

### 结论

- 当前正式主线和 15 JD MVP 数据闭环应保留；剪枝重点不是核心业务模块，而是已经完成
  使命的一次性补齐/对比工具、重复入口、绕过现行候选与人工定稿边界的旧内部编排、
  专属回归测试，以及记录迭代过程而非当前事实的文档段落。
- 第一轮高置信度代码剪枝预计可删除约 3,600 行一次性脚本和专属测试；再删除
  `app/extraction.py` / `app/consolidation.py` 中约 250 行旧的“模型调用后直接写正式表”
  编排。正式 CLI、候选生成、finalize、安全门、统计、报告和 E2E 不受影响。
- `data/private/`、`data/raw_jds/`、项目数据库、当前正式报告、人工裁决与 frozen-base
  证据链不自动删除。它们是私有正式数据或 provenance，不因未被 Git 跟踪就视为缓存。
- `reports/P0-7/legacy-extraction-waiver.json` 是 `generate-report` 的运行时安全门，必须保留；
  JD 1～3 继续保持 `unverified`，不得借剪枝回填或隐藏风险。
- 本轮只完成分析和分类，没有实际删除功能、数据或本地材料，没有调用付费模型。

### 判断标准

文件或符号满足下列任一条件才建议删除：

1. 当前正式 CLI、验收链、finalize、审计、统计、报告和公开 sample 均无消费方；
2. 只服务已经结束的旧批次回填、3→5 JD 对比或旧格式兼容；
3. 与 `app.cli` 正式入口重复，且只增加第二套参数/测试维护面；
4. 允许模型结果未经当前候选隔离、完整验收和人工审核直接进入正式表；
5. 文档内容只是迭代日志，当前事实已由数据库、Git 或精简后的状态摘要覆盖。

以下内容不能仅凭“当前无调用”删除：正式 provenance、隐私数据、安全门、Schema/证据合同、
失败回滚测试、付费调用保护和当前批次的人工裁决链。

## 文件分类清单

### A. 建议第一轮直接删除（高置信度）

| 文件/符号 | 结论 | 依据 |
|---|---|---|
| `scripts/experiments/p0_3/finalize_extraction.py` | 删除 | 仅包装 `app.extraction_finalization.finalize_extraction`；正式入口已是 `app.cli finalize-extraction` |
| `scripts/experiments/p0_4/finalize_consolidation.py` | 删除 | 仅包装 `app.consolidation_finalization.finalize_consolidation`；正式入口已是 `app.cli finalize-consolidation` |
| `scripts/experiments/p0_3/verify_extraction_source.py` | 删除 | 为旧格式验收产物补字段；JD 4～15 已 fully_bound，JD 1～3 已明确不回填并走 waiver |
| `scripts/experiments/p0_4/backfill_consolidation_metadata.py` | 删除 | 一次性补齐旧批次 #1/#2；任务已完成，现行批次由 finalize 原生写入完整元数据 |
| `scripts/experiments/p0_4/compare_incremental.py` | 删除 | 只服务已关闭的 3→5 JD 增量比较；15 JD 当前路径已由 frozen-base 约束取代 |
| `scripts/experiments/p0_4/run_small_scale_precheck.py` | 删除 | 阶段性小样本成本预检；当前已有正式候选入口与完整 `run_acceptance`，不属于关闭后 MVP 必需入口 |
| `tests/test_verify_extraction_source.py` | 删除 | 只验证上述旧格式补齐工具 |
| `tests/test_backfill_consolidation_metadata.py` | 删除 | 只验证上述一次性归并回填工具 |
| `tests/test_compare_incremental.py` | 删除 | 只验证已结束的 3→5 JD 比较脚本 |
| `tests/test_consolidation_run.py` | 删除 | 只验证未被正式 CLI 使用的直接模型→正式表旧编排 |
| `tests/test_consolidation_persist.py` | 删除 | 主要证明同一旧直接编排；正式 finalize、幂等和回滚已有 `test_p0_4_finalize.py` 覆盖 |
| `data/golden/jd_extractions/.gitkeep` | 删除 | 当前验证协议已明确旧完整 Gold/F1 不属于正式验收；空目录占位无消费方 |
| `.gitignore` 中 `data/golden/jd_extractions/*` 规则 | 删除 | 随旧 Gold 目录一起移除，避免继续暗示该目录属于当前方案 |

同时删除下列旧内部符号，但保留所在核心模块：

- `app/extraction.py`：`ExtractionFailure`、`ExtractionSummary`、`persist_extraction()`、
  `extract_jobs()`。这些符号实现模型结果直接写正式抽取表；当前正式路径只允许候选 JSON
  经完整验收后由 `finalize-extraction` 写入。
- `app/consolidation.py`：`ConsolidationFailure`、`ConsolidationSummary`、
  `consolidate_requirements()`。该函数直接调用模型并持久化归并；正式 CLI 不调用它。
  `persist_consolidation()` 必须保留，因为正式 `finalize-consolidation` 与公开 sample 仍使用。
- `tests/test_extraction.py`：删除只覆盖 `persist_extraction()` / `extract_jobs()` 的用例，
  保留模型响应解析、证据校验、重试和两段式抽取合同用例。
- `tests/test_p0_4_acceptance.py`：删除 `run_small_scale_precheck` 专属用例，保留完整验收、
  顺序变形、order-only resume、付费门禁和数据库身份门禁。

### B. 建议精简或同步，不整文件删除

| 文件 | 处理 |
|---|---|
| `docs/CURRENT_STATE.md` | 从 465 行压缩为当前可运行入口、15 JD/#5 正式数据、有效 provenance 例外、已知限制和“无获授权下一阶段”；删除按日期记录的 3→5→8→12→15 执行过程 |
| `docs/annotation/VALIDATION.md` | 保留现行 evidence/coverage/验收协议；删除旧人工 Gold/F1 历史叙述，把错误的“用 v0.9 重新抽取”统一为 v0.10 |
| `README.md` | 保留产品说明和正式命令；修正文档合同测试要求与 PowerShell 换行命令不一致的问题 |
| `docs/PROJECT_PLAN.md` | 保留简短阶段状态、当前例外和当前边界；不再扩写新的执行时间线 |
| `app/extraction.py`、`app/extraction_two_stage.py` | 删除 v0.6～v0.9 Prompt 演进注释，只保留 v0.10 + Schema V3 当前合同 |
| `app/schemas.py`、`app/extraction_validation.py` | 继续拒绝旧 Schema 值，但用户提示改为使用当前 v0.10 重新抽取，不保留旧版本迁移叙述 |
| `app/extraction_finalization.py` | 删除指向 `verify_extraction_source` 的旧格式复核说明；继续明确拒绝旧格式 |
| `scripts/README.md`、`app/README.md` | 按删除后的正式/实验边界同步入口表，保持简短 |
| `tests/test_finalize_extraction.py`、`tests/test_p0_4_finalize.py` | 从重复实验 wrapper 改为直接测试 core 或正式 CLI，保留定稿身份、幂等、冲突和回滚覆盖 |
| `tests/test_pipeline_e2e.py` | 保留正式全链 E2E；修正文档命令断言，使其验证参数存在而不依赖 Markdown 是否换行 |

### C. 必须保留的受版本控制文件

根目录与公共说明：

- `AGENTS.md`、`README.md`、`.env.example`、`.gitignore`、`.python-version`、
  `pyproject.toml`、`uv.lock`。

正式应用实现：

- `app/cli.py`、`app/config.py`、`app/database.py`、`app/models.py`、
  `app/ingestion.py`、`app/schemas.py`；
- `app/candidates.py`、`app/extraction.py`、`app/extraction_two_stage.py`、
  `app/extraction_validation.py`、`app/evaluation.py`；
- `app/requirement_consolidation.py`、`app/consolidation.py`、
  `app/consolidation_validation.py`；
- `app/extraction_finalization.py`、`app/consolidation_finalization.py`、
  `app/finalization.py`；
- `app/market_analysis.py`、`app/market_report.py`、`app/__init__.py`、`app/README.md`。

仍属当前质量与人工审核链的脚本：

- `scripts/experiments/p0_3/run_acceptance.py`；
- `scripts/experiments/p0_3/run_real_jd_acceptance.py`；
- `scripts/experiments/p0_4/run_acceptance.py`；
- `scripts/experiments/p0_4/analyze_stability.py`；
- `scripts/experiments/p0_4/apply_review_decisions.py`（包含当前 frozen-base 安全门）；
- `scripts/make_sample_report.py`、包 `__init__.py` 与 `scripts/README.md`。

当前合同、公开产物和安全门：

- `data/rule_scenarios/extraction_metamorphic_cases.json`、`data/raw_jds/.gitkeep`；
- `docs/ARCHITECTURE.md`、`docs/GLOSSARY.md`、`docs/PROJECT_PLAN.md`、
  `docs/annotation/REQUIREMENTS.md`、`RESPONSIBILITIES.md`、`VALIDATION.md`；
- `examples/market-report-sample.md`；
- `reports/P0-7/legacy-extraction-waiver.json`。

保留的测试类别：

- Schema、数据库、导入、抽取客户端/两段式/合同/变形：`test_schemas.py`、
  `test_database.py`、`test_ingestion.py`、`test_extraction.py`、
  `test_extraction_two_stage.py`、`test_extraction_validation.py`、
  `test_extraction_metamorphic.py`、`test_evaluation.py`；
- 归并输入、客户端、合同、稳定性和 frozen-base：`test_consolidation.py`、
  `test_consolidation_client.py`、`test_requirement_consolidation.py`、
  `test_consolidation_validation.py`、`test_p0_4_acceptance.py`、
  `test_p0_4_stability.py`、`test_p0_4_frozen_base.py`；
- 正式定稿、审计、统计、报告和全链：`test_finalize_extraction.py`、
  `test_p0_4_finalize.py`、`test_cli.py`、`test_market_analysis.py`、
  `test_market_report.py`、`test_pipeline_e2e.py`、`test_experiment_scripts.py`。

### D. 本地忽略文件分类（不在第一轮自动删除）

可安全再生、实施时可直接清理：

- 仓库代码目录中的 `__pycache__/`、`*.pyc`、`.pytest_cache/`、`.ruff_cache/`；
- 不清理 `.venv/` 内部缓存，若环境损坏应整体删除 `.venv/` 后由 `uv sync` 重建。

建议删除但必须在实施时再次确认精确路径：

- `data/golden/jd_extractions/jd_001.json`～`jd_005.json` 和本地 README：旧 Gold
  材料已不属于当前验收；
- `reports/P0-4/acceptance-20260804-final.json`、`acceptance-5jd.json`、
  `consolidation-backfill-1.json`、`consolidation-backfill-2.json`、
  `final-consolidation-5jd-summary-v2.json`、`module4-3to5-comparison.json`、
  `precheck.json`、`previous-batch-note.json`、`previous-batch-2-note.json`、
  `stability-report.json` 与 `reports/P0-5/market-report-3.md`：只描述已结束的
  早期批次或一次性维护操作。

必须保留或先建立单独备份策略：

- `data/jd_skill_insight.db`、`data/raw_jds/`、`data/private/` 全部内容；
- P0-7 waiver 中列为既有证据的 JD1～3 验收报告；
- JD4～15 fully_bound 抽取所绑定的验收 report/raw；
- 12 JD frozen-base 正式结果、15 JD acceptance/order-retry、审核决定、最终 candidate
  和 15 JD 正式报告。它们共同构成批次 #5 的私有 provenance。

数据库文件名 `jd_skill_insight.db` 暂不更名：它是当前正式私有数据库目标，改名不是剪枝，
且会扩大到路径切换和数据误用风险。

## 建议实施顺序

1. **安全边界收口**：先删除 `extract_jobs()` / `consolidate_requirements()` 直接写正式表路径，
   调整对应测试，证明正式写入只剩两个 finalize 入口。
2. **一次性工具剪枝**：删除重复 finalize wrapper、verify/backfill/compare/precheck 及专属测试；
   更新 CLI E2E 和文档入口引用。
3. **文档剪枝**：压缩 `CURRENT_STATE.md`，清理旧版本提示和迭代时间线，修复 README
   文档合同测试。
4. **本地生成物清理**：先列出并核对绝对路径，只删除缓存、旧 Gold 和明确列出的早期报告；
   不触碰私有正式 provenance。
5. 每个内聚模块分别运行相关测试；全部完成后运行 `uv run pytest --basetemp .pytest-tmp`
   与 `uv run ruff check app scripts tests`。不调用模型、不写正式数据库。

建议拆成三个提交：`refactor(pipeline)` 正式写入边界收口、`chore(scripts)` 一次性工具剪枝、
`docs(project)` 当前文档收口。任何一步若发现正式入口或 #5 provenance 的真实消费方，立即将
对应文件移回“保留”类别，不为追求删除数量扩大风险。

## 本轮验证

- `uv run ruff check app scripts tests`：通过；
- 全量测试：378 passed、1 failed；唯一失败为已知的
  `test_documented_commands_and_paths_exist`，原因是 README 的 PowerShell 命令换行，而测试
  要求 `extract-jds --all --candidate-output` 为连续字符串；本轮仅修改 Review Log，未新增失败；
- `uv run pytest` 在当前本地更名后的虚拟环境偶发报 `uv trampoline failed to canonicalize
  script path`，因此使用 `.venv/Scripts/python.exe -m pytest` 完成全量执行；
- `git diff --check` 通过；未调用模型，未读写正式数据库，未删除本地文件。
