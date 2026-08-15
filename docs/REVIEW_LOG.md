# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：实施仓库剪枝（2026-08-15）

### 结论

剪枝已完成，当前主线未改变。正式业务表的模型写入边界已收口为
`finalize-extraction` 与 `finalize-consolidation`；一次性回填、旧批次比较、
小规模预检和重复 finalize 包装入口已删除。归并定稿与人工裁决不再兼容缺少顶层身份
字段的验收 raw，也不再从单次运行 metadata 推断身份。

相对剪枝分析基线，本轮实现修改 33 个文件，新增 255 行、删除 4,748 行。保留的
`scripts/experiments/` 只剩当前抽取验收、真实 JD 验收、归并验收、稳定性分析和人工
裁决应用。

### 文件分类与处理

| 分类 | 文件 / 符号 | 处理 |
|---|---|---|
| 删除：重复正式入口 | `scripts/experiments/p0_3/finalize_extraction.py`、`scripts/experiments/p0_4/finalize_consolidation.py` | CLI 已提供唯一正式入口 |
| 删除：一次性旧批次工具 | `verify_extraction_source.py`、`backfill_consolidation_metadata.py`、`compare_incremental.py`、`run_small_scale_precheck.py` | 只服务已结束批次、回填或已被完整验收替代 |
| 删除：绕过当前写入边界的 API | `app.extraction.persist_extraction/extract_jobs`、`app.consolidation.consolidate_requirements` 及其 summary/failure 类型 | 防止模型调用后直接写正式表；保留定稿所需底层 `persist_consolidation` |
| 删除：专属测试 | `test_verify_extraction_source.py`、`test_backfill_consolidation_metadata.py`、`test_compare_incremental.py`、`test_consolidation_persist.py`、`test_consolidation_run.py` 及预检专属用例 | 随被删实现一并移除；现行合同测试保留 |
| 删除：空目录占位 | `data/golden/jd_extractions/.gitkeep` 及其 Git 例外规则 | 目录仍整体忽略，本地内容未删除 |
| 保留：当前实验入口 | P0-3 `run_acceptance.py`、`run_real_jd_acceptance.py`；P0-4 `run_acceptance.py`、`analyze_stability.py`、`apply_review_decisions.py` | 仍服务当前完整验收和人工定稿链路 |
| 更新：当前事实文档 | `README.md`、`app/README.md`、`docs/CURRENT_STATE.md`、`docs/annotation/VALIDATION.md` | 删除过程性历史，统一 v0.10 + Schema V3 / Prompt 4.3 与候选—验收—定稿边界 |
| 更新：当前合同 | `app/consolidation_finalization.py`、`apply_review_decisions.py` | raw 顶层身份字段缺失即拒绝，不保留旧格式 fallback |

### 保守保留的本地材料

按本轮授权，未删除任何未跟踪或被 Git 忽略的历史 artifact。检查时本地仍有：

- `data/golden/jd_extractions/` 下 6 个文件；
- `reports/` 下 27 个未跟踪报告文件；
- 其他私有、原始 JD、数据库与模型响应均未纳入删除范围。

这些材料不影响公开仓库整洁度。若后续需要，可另行盘点后移动到项目外归档；本轮不建议
自动删除。

### 验证

- `uv run ruff check app scripts tests`：通过；
- `uv run pytest --basetemp .pytest-prune-full`：Windows 环境中的 uv pytest
  trampoline 无法规范化脚本路径；
- 使用同一项目虚拟环境执行
  `.venv\\Scripts\\python.exe -m pytest --basetemp .pytest-prune-full`：
  **336 passed**；
- 相关合同与文档测试：**57 passed**；
- 未调用任何付费模型，未写项目数据库。

### 提交

- `6da74e4 refactor(pipeline): 收口正式数据写入边界`
- `1b80cad chore(scripts): 删除一次性实验工具`
- `a525bb6 refactor(contract): 删除非当前格式回退`
- 本文件随本轮最终总结单独提交。

当前状态：剪枝实施完成；公开仓库只保留当前 MVP 主线及其验收/定稿支持代码。本轮提交尚未
推送。
