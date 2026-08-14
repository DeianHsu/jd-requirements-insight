# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：15 JD review apply 冻结基线 blocker（2026-08-14）

### 任务内容

- 只读检查现有 12 JD review decisions、`apply_review_decisions.py` 与正式 12 JD final
  candidate，评估能否表达“冻结 IDs 1～300，只应用 15 JD 新增裁决”。
- 使用 run1 + 12 JD 的 34 条 decisions / 9 条 canonical name overrides 做内存模拟；未写
  review-decisions、未生成 final candidate，未调用模型、未重跑或 finalize consolidation。

### 验证结果

- 正式 12 JD 基线仍为 241 canonical / 300 mappings；run1 限制到 IDs 1～300 后只有
  229 个 cluster，与冻结 partition 不相等：缺少 10 条已批准 old pair，同时包含 49 条
  未批准 old pair；25 个 run1 old cluster 不属于冻结分区，37 个冻结 cluster 未在 run1
  原样出现。
- 现有 apply 入口只能从 `selected_run["result"]` 开始，没有 frozen-base 参数；must-link
  会把被指定 ID 所在 owner canonical 的全部成员一起搬入 primary，而不是只移动指定 ID。
- 将 12 JD 决定原样应用到 run1 的内存模拟在结果模型校验处失败：拆分后出现重复
  canonical names；即使追加名称 override，也不能证明旧 partition 已恢复。

### 当前 blocker 与最小解决方案

- 当前 review apply 机制无法把正式 12 JD final partition 作为不可变基线，因此不能安全
  生成本轮 15 JD candidate；硬塞额外 old↔old decisions 或手工改结果都会把冻结事实误写成
  新语义裁决，且存在 owner canonical 被整体拖入的风险。
- 最小机制补充：为现有离线 apply 增加一个显式、强校验的 frozen base 输入，只接受已
  final 的 12 JD result（精确 IDs 1～300 / 241 canonical / 300 mappings / 指纹匹配）；
  在该基线上将 301～409 作为待裁决成员，再复用现有 decisions、名称 override、覆盖与
  指纹输出。输出必须额外记录 frozen result fingerprint，并在结束时逐 ID 比较旧 partition。
- 这是 apply 的单点能力补充，不修改 Prompt/Schema/数据库，也不处理 order hard gate。

### 当前状态

按用户停止条件，本轮未创建 15 JD review-decisions 或 final candidate。等待外部确认上述
最小 frozen-base apply 补充后再实施；order transformation hard gate=1 仍是后续独立 blocker。
