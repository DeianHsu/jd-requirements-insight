# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：P0-7 waiver 门禁与 JD 9～15 免费预检（2026-08-09）

### 任务内容

- `generate-report` 在存在 non-fully-bound 来源时实际读取 P0-7 historical
  waiver，校验记录类型、版本、批准信息、证据、`generate-report` 用途、
  新增记录边界和适用 job_ids；缺失、非法或范围外未绑定 JD 均拒绝生成。
- JD 1～3 继续保持 `unverified`；合法豁免时允许现有批次生成报告并保留
  provenance 风险提示。没有改 Schema 或抽取/归并语义。
- PROJECT_PLAN 增加 P0-8 当前阶段：8 JD 已关闭，当前 8→12，之后 12→15，
  15 为 MVP 固定终点。
- 对 JD 9～15 完成免费预检和两批 dry-run，未调用付费模型。

### 验证结果

- waiver/report 相关测试：33 passed；正式 E2E：4 passed；全量：357 passed；
  `ruff check app scripts tests`：通过。
- 真实批次 #3（JD 1～8）使用仓库 waiver 离线生成成功并保留 provenance
  提示；临时报告已删除。
- JD 9～15 文件与数据库 ID 一致、无抽取记录、无精确重复；与 JD 1～8
  最高文本相似度 9%～20%，新增样本间最高约 25%，未发现近重复。
- 7 份岗位均属于 AI 应用 / LLM 应用 / Agent 工程范围。非阻塞完整性风险：
  JD 10 职位描述较短；JD 12 公司名、JD 13 职位名在截图中被截断并原样
  保留。其余未发现明显缺失。

### 当前状态与下一步

JD 9～15 可进入正式主线。下一批确定为 JD 9～12：deepseek-v4-flash、
Prompt 0.10、Schema 3.0、每 JD 3 runs、每阶段 max_attempts=2；必须等待
用户授权后才执行 `--execute`。12 JD 批次无真实阻塞后再处理 JD 13～15。

### 执行提交

- 提交 waiver 门禁、必要测试和当前事实文档；未修改正式数据库、原始 JD
  或任何模型产物。
