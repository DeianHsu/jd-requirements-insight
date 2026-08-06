# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务
完成时覆盖更新，只保留最新一轮；历史由 Git 保存。项目状态以
`docs/CURRENT_STATE.md` / `docs/PROJECT_PLAN.md` 为准。

## 最近一轮：8 JD 扩样第一批次——导入 + JD 6/7/8 抽取验收（2026-08-07）

### 任务内容（按用户单一路线执行，未改代码、未扩展范围）

1. **JD 导入**：新增 jd_006（思必驰科技/AI应用开发工程师/苏州）、
   jd_007（浩鲸科技/大模型应用开发工程师/南京）、jd_008（苏州凰凤
   飞行器技术/AI算法工程师/苏州，无人机 CV 方向，用户提供的截图整理，
   经两次核对修正公司名"鹰→凰"）；导入 3 新增 + 5 幂等跳过，旧 JD 1~5
   未修改、未重复导入；
2. **免费预检**：`run_real_jd_acceptance --dry-run --job-ids 6 7 8`
   通过；确认冻结基线一致（deepseek-v4-flash / prompt 0.10 / schema 3.0）；
3. **第一次付费验收失败**（用户授权后执行）：仅 jd_006 run0 成功
   （14 条），其余 8 次 `Connection error`，hard_gate=11。排查：LLM
   配置完整、`GET https://api.deepseek.com/models` 返回 200 且
   deepseek-v4-flash 在列表，判定为时段性网络/服务端故障，非代码缺陷；
4. **第二次付费验收成功**（用户授权重跑，同命令同参数）：
   - 9/9 次运行全部完成：jd_006 = 12/11/11 条、jd_007 = 43/45/43 条、
     jd_008 = 20/18/20 条；
   - **hard_gate_failures = 0**（passed=True）；
   - warnings = 6（全部为运行间稳定性漂移 unmatched_item_count 3~6，
     与 JD 1~5 验收已知类型一致，非阻塞）；diagnostics = 2；
   - 产物：report（脱敏）`reports/P0-3/real-jd-acceptance-20260806-214825-report.json`、
     raw（私有）`data/private/experiments/p0_3/real_jd/real-jd-acceptance-20260806-214825-raw.json`；
   - 整轮身份 8 字段齐全：run_identifier=real-jd-acceptance-20260806-214825、
     model=deepseek-v4-flash、prompt=0.10、schema=3.0、job_ids=[6,7,8]、
     jd_set_fingerprint=67706727…、runs=3、max_attempts=2，满足
     finalize-extraction 定稿合同；
   - 实际模型调用：9 运行 × 2 段 ≈ 18 次（无重试）。

### 说明与风险

- P0-3B 验收协议为 3 次独立运行，不含顺序变形（顺序变形属于 P0-4
  归并验收 `run_acceptance`）；
- jd_008 为无人机 CV 方向，与 MVP 分析目标（LLM/Agent 应用工程）有
  差异，归并后可能产生长尾 singleton，已向用户提示，保留待裁决；
- 第一次失败产物（real-jd-acceptance-20260806-213527-*）保留未清理，
  非正式数据。

### 执行提交

- 本轮无代码改动、无新 commit（JD 文档在 `data/raw_jds/`，属私有
  输入不入库）；仅本评审日志覆盖更新，随下一提交一并推送。

### 当前状态

- 8 JD 全部在库（ID 1~8）；JD 6/7/8 抽取验收通过，等待**人工审核**
  → 审核通过后 `finalize-extraction` 定稿 → 8 JD 全量归并（P0-4
  验收 + 稳定性 + 人工裁决）→ 报告；
- 后续步骤均需用户指令/授权（人工审核、归并付费调用）。
