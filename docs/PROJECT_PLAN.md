# 项目计划

## 目标

把真实 JD 转化为**可统计、可追溯的市场要求报告**：导入 → v0.10 + Schema V3
结构化抽取 → 抽取质量验证 → requirement instance 归并为 canonical
requirement → 独立 JD 统计 → 原文证据追溯 → Markdown 市场分析报告。

## 六个当前阶段

| 阶段 | 目标 | 状态 |
|---|---|---|
| P0-1 数据合同与语义规则 | 抽取数据合同（Schema V3 三级熟练度）、要求/职责/逻辑组语义规则、证据合同 | ✅ 已完成 |
| P0-2 v0.10 结构化抽取 | 两段式（发现段 + 判断段）、证据校验、有限重试、幂等持久化 | ✅ 已完成（真实模型验证已通过） |
| P0-3A 规则场景验证 | 领域中性场景 + 确定性变换，合同检查与变形属性检查 | ✅ 已完成（Prompt 0.10，13 场景 hard gate=0） |
| P0-3B 真实 JD 验证 | 显式选择 JD、重复运行、合同/漂移检查、异常项索引 | ✅ 已完成（JD 1/2/3 累计 hard gate=0，人工审计通过） |
| P0-4 要求事实归并 | instance → canonical → 唯一映射；合同校验、positive-pair Jaccard、漂移与变形检查 | 🟡 流程就绪（v0.10 输入已就绪，验收待执行） |
| P0-5 市场统计、证据追溯与 Markdown 报告 | `app/market_analysis.py` 统计模块已完成；`generate-report` 与证据追溯报告待实现 | 🟡 进行中 |

## 当前下一步

1. 执行 P0-4 小规模预检与正式验收（按真实 requirement instance 数量
   选择 target-size，不超过实际可用数量）；
2. 生成并离线验证正式归并批次；
3. 验收通过后实现 `generate-report`。

边界与硬依赖见 `AGENTS.md`（不维护旧版本兼容、付费调用必须
`--execute`、私有材料不提交 Git、MVP 轻量开发原则）。
