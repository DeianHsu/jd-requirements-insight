# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。当前项目事实以 `docs/CURRENT_STATE.md` 为准。

## 最近一轮：v0.1 当前完成度 L3 复核（2026-08-24）

### 总结结论

v0.1 在已经声明的 CLI 市场分析范围内完成，可继续冻结维护；它不等于 Web 招聘产品、
简历匹配、ATS、自动投递 Agent 或下一阶段已经完成。当前基线没有 blocker。

### 功能与实现核实

- Markdown JD 可校验导入 SQLite，并通过规范化内容哈希去重；
- v0.10 + Schema V3 两段式抽取区分 responsibility / requirement / mixed / excluded，
  校验证据、原子化、逻辑组、字段合同和覆盖；
- 抽取与归并均以多次 acceptance + 人工审核/裁决 + finalize 正式化，单次 candidate
  只写私有预检 JSON，不写模型生成的正式数据；
- extraction finalize 与 consolidation finalize 校验 report/raw、范围、版本、批准运行、
  结果和文件指纹、精确 ID 覆盖，并具备幂等与失败回滚；
- 归并由模型决定 canonical 来源分区，代码确定性生成唯一 mappings；稳定性分析包含
  顺序变形、positive-pair Jaccard、数量和 singleton 漂移；
- 统计按独立 JD 计数，报告从正式归并回查 requirement → extraction → JD evidence，
  并在上游 provenance 不完整时执行私有 waiver 门禁与风险披露；
- 数据库目标、付费 `--execute`、当前 Schema、隐私目录和只读审计入口均有显式保护。

### L3 证据

- 私有 release manifest 的 8 个权威 artifact 与 waiver SHA-256 全部匹配；
- 正式批次只读复核：15 JD、409 requirement instances、329 canonical requirements、
  409 mappings、coverage 100%、结构违规 0、`reportable=True`；
- 正式 `audit-extraction-sources`、`audit-consolidation`、`validate-consolidation` 和
  `generate-report` 均实际执行成功，临时报告已删除；
- 生产 E2E 测试调用真实 acceptance 脚本、审核绑定、两个 finalize、统计与报告入口；
- 12 个代表性正常/失败路径通过，覆盖付费确认、显式数据库、身份混用、幂等、回滚、
  未覆盖来源和范围缺失；全量测试 **337 passed**，Ruff、sample、11 份文档链接通过。

### 边界与风险

- 当前基线 blocker：无；下一阶段 blocker：没有获授权的下一实施阶段；
- 长期规模化风险：真实样本仅 15 份；最低 positive-pair Jaccard 为 28.85%，归并仍需
  人工裁决；少量早期抽取保持 `unverified`，只由范围受限 waiver 放行并披露风险；
- 非阻塞环境项：当前 Windows uv cache 权限导致 `uv run` 无法启动，本轮使用同一项目
  虚拟环境完成等价测试；终端中文显示存在环境编码乱码，不影响命令退出状态和产物；
- 未调用付费模型，未写正式数据库，未修改私有 artifact；本文件随复核总结提交。
