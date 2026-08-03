# 抽取版本批准·人工汇总记录（final review）

> 用途：v0.8 + Schema V3 的最终批准结论由人工汇总确认后输出。
> 验收脚本只计算自动 hard gate（`passed`）；`decision_eligible` 恒为 False，
> 直到本记录完成全部确认项。本模板不包含真实 JD 内容；报告与审计引用只指向
> 私有输出位置。

## 确认项（全部完成后才可输出批准结论）

- [ ] **Track A passed**：`reports/P0-3/<run-tag>-report.json`（合成规则场景，
      hard gate failures 为空，`--phase acceptance` 运行）
- [ ] **Track B passed**：`reports/P0-3/<run-tag>-report.json`（真实 JD，
      hard gate failures 为空，`--phase acceptance` 运行）
- [ ] **human audit completed**：人工违规审计按规则 ID 完成，无未处理的高
      严重度违规（审计记录见 `reports/templates/extraction-rule-audit.json`
      模板，`evidence_reference` 指向私有输出）
- [ ] **threshold decision recorded**：多次运行稳定性阈值已由用户裁决并
      冻结（记录阈值数值与适用范围），验收运行使用的规则、范围与阈值与
      冻结配置一致

## 记录字段

| 字段 | 值 |
|---|---|
| extractor_version | `model|prompt:0.8|schema:3.0`（实际 model） |
| protocol_version | `data/rule_scenarios/extraction_metamorphic_cases.json` 的 `protocol_version` |
| track_a_report | 报告文件路径（脱敏） |
| track_b_report | 报告文件路径（脱敏） |
| audit_report | 审计记录文件路径（私有） |
| threshold_frozen_at | 阈值冻结日期与冻结配置引用 |
| reviewed_at | 本记录完成日期 |
| reviewer | 批准人 |
| decision | APPROVED / NOT APPROVED / 条件性批准（注明条件） |
