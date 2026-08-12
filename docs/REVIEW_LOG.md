# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：12 JD canonical 名称门禁收口（2026-08-12）

### 任务内容

- 为现有 `apply_review_decisions.py` 增加最小
  `canonical_name_overrides` 能力：所有分区决定应用后，按最终 canonical 的
  完整 requirement IDs 精确定位并改名，只改变名称，不改变 partition。
- 未放松 canonical_name 唯一性、未新增 merge、未硬编码本批 IDs、未修改
  Prompt/Schema、未调用模型、未 finalize、未处理 JD13～15。
- 当前 12 JD 私有 decisions 加入 9 个外部 Review 冻结名称 override，并对
  independent run2 重新执行正式离线 apply。

### 验证结果

- Source 为 `run-2`，结果指纹 `e091f2cc…ec6a5`；input fingerprint 与当前
  JD1～12 / 300 instances 数据库输入一致。
- Decisions 指纹 `7170abe0…8c2a6`；final result 指纹
  `47591259…e052f`，source/decisions/final 指纹均与 candidate 和 summary
  完整绑定。
- 最终 241 canonical、300 mappings、coverage=100%、结构违规=0、名称唯一、
  exact ID coverage 通过，与外部 Review 预期完全一致。
- 8 JD 历史 adjudication、问题能力边界、20 组 any_of/parallel-item 拆分、
  新增 must-link、9 个名称 override 及 212 Python 工程能力独立边界全部通过。
- 新增测试覆盖：无 override 的旧行为、只改名不改 partition、唯一名称门禁、
  非法/重复/无法定位 override 拒绝；相关测试 29 passed，Ruff 通过。
- 全量测试 365 passed；`ruff check app scripts tests` 通过。

### 当前状态与下一步

12 JD 离线 candidate 与脱敏 summary 已生成；正式数据库仍只有 consolidation
#1～#3，未执行 finalize-consolidation 或 generate-report。等待外部 Reviewer
审核 candidate 后再决定是否正式定稿。

### 执行提交

- 提交最小 name override 能力、核心回归测试与三份状态文档；私有 decisions、
  candidate、真实数据库和验收 raw 保持 Git 忽略。
