# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：P0-7 关闭——JD 1~3 历史豁免最小收口（2026-08-07）

### 任务内容（按用户单一路线执行，未扩展范围）

采用"明确接受历史例外"方式关闭 P0-7：不重新验收、不回填旧记录、
不建设通用豁免系统。只做四件事：

1. 新增脱敏历史例外记录 `reports/P0-7/legacy-extraction-waiver.json`
   （适用对象 JD 1/2/3；原因：记录产生于现行定稿绑定合同建立之前；
   已有依据：原 P0-3B 验收 hard gate=0 + 人工语义审计通过；允许用途：
   仅当前 MVP 归并/统计/报告；风险：无法机器证明完整来源绑定；
   状态：保持 `unverified`，不写成 `fully_bound`；批准人
   project-owner、批准时间 2026-08-07；限制：仅限明确列出的历史记录，
   新增 JD 禁止使用）；
2. `docs/PROJECT_PLAN.md`：P0-7 改为 `✅ 已关闭`，例外 1 处置状态改为
   `✅ 已豁免`（引用豁免记录路径），「当前下一步」改为扩样计划
   （8→12→15 JD，固定终点）；
3. `docs/CURRENT_STATE.md`：「正式生产主线」段写入 P0-7 关闭声明与
   豁免记录；「下一步」改为扩样阶段执行顺序；明确"例外不等于完整
   来源绑定"、报告 provenance 风险提示保留、新增 JD 必须走现行正式主线；
4. `docs/REVIEW_LOG.md` 覆盖更新（本文件）。

### 未改动项（按任务禁令核对）

- 未新增数据库表 / 未改 Schema / 未新增迁移；
- 未新增豁免管理 CLI 或通用豁免模型/服务；无签名、撤销、版本、审批
  工作流；
- 未修改 `audit-extraction-sources` 分类算法与名称（JD 1~3 仍
  `unverified`、JD 4~5 仍 `fully_bound`）；
- 未回填虚构指纹、未重新调用模型验收 JD 1~3；
- 未修改 `generate-report` 的 provenance 风险提示逻辑（`app/cli.py`
  未动，unverified 上游仍显式标注风险）；
- 未修改任何业务代码（本次为纯数据文件 + 文档变更）。

### 验证结果

- `uv run python -m ruff check app scripts tests`：全过；
- `uv run python -m pytest --basetemp .pytest-tmp`：352 passed
  （系统 Temp 被锁定属已知环境问题，必须加 `--basetemp .pytest-tmp`）。

### 当前状态

- P0-7 = `✅ 已关闭`；P0-1～P0-7 全部关闭；JD 1~3 为已批准历史豁免
  （例外不等于完整来源绑定，报告风险标注保留）；
- 下一阶段：扩样（用户提供新增真实 JD → 8 JD 批次 → 12 → 15，
  固定终点，不扩展到 20）；每批只执行现有正式主线，付费调用前汇报
  模型/JD 范围/目的/命令并等待授权。
