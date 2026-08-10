# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：12 JD run2 裁决离线应用（2026-08-10，阻塞）

### 任务内容

- 从 12 JD acceptance raw 读取 independent run2（`run_index=2`，结果指纹
  `e091f2cc…ec6a5`），创建绑定 JD1～12、300 instances 与当前 input
  fingerprint 的私有裁决文件；共 34 条决定，拆分决定全部位于依赖它们的
  Java/Go must-link 之前。
- 使用现有 `apply_review_decisions.py` 对 run2 做纯离线 apply；未调用模型、
  未修改 Prompt/Schema、未 finalize、未生成报告、未处理 JD13～15。

### 验证结果

- 裁决文件 SHA-256 为 `adf1023c…5dabb`；身份预检通过。严格按决定顺序做
  ID partition 模拟得到 241 canonical、300 mappings、300 个唯一 ID；全部
  指定历史裁决、新增 must-link、问题边界、Python 工程能力边界及 20 组
  any_of/parallel-item 拆分均成立。
- 正式 apply 在候选构造阶段被 canonical 名称唯一性合同拒绝：`Python`
  出现于既有组和拆出的 112/160，`C++` 出现于既有 195 和拆出的 87，
  `Java` 出现于既有 156 和新合并的 88/159，`LangChain` 出现于既有 71
  和拆出的 116。未写出 final candidate 或 public summary。
- 这不是 241/300 分区数量偏差；追加合并会改变预期数量，擅自改名又超出
  当前裁决。按 Reviewer 的停止条件，没有调整决定凑数或绕过校验。
- 全量测试 357 passed；`ruff check app scripts tests` 通过。首次 pytest 启动
  使用旧 `.pytest-tmp` 时受 Windows 目录锁影响，改用新的仓库内 basetemp
  后全量通过。

### 当前状态与下一步

run2 与裁决身份已确定，但 12 JD 离线候选尚未生成。需外部 Reviewer 对四类
同名且当前要求保持独立的 canonical 明确唯一名称，或明确新增合并裁决；之后
才能重新 apply。正式数据库仍只有 consolidation #1～#3，未 finalize，
JD13～15 未动。

### 执行提交

- 本轮仅提交当前状态与评审摘要；私有 review-decisions、正式数据库、真实
  验收报告、原始 JD 和模型产物均保持 Git 忽略。
