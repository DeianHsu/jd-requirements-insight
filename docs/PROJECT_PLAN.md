# 项目计划

## 目标

把真实 JD 转化为**可统计、可追溯的市场要求报告**：导入 → v0.10 + Schema V3
结构化抽取 → 抽取质量验证 → requirement instance 归并为 canonical
requirement → 独立 JD 统计 → 原文证据追溯 → Markdown 市场分析报告。

## 阶段状态语义

| 状态 | 含义 |
|---|---|
| `未开始` | 尚未启动 |
| `进行中` | 主体开发中 |
| `🔵 待收口` | 主体实现完成，真实链路验证 / 接口收口 / **历史例外处理未完且属于阶段目标范围**（未正式关闭） |
| `✅ 已关闭` | 正式关闭，关闭条件全部满足 |
| `🟡 已关闭·含例外` | 正式关闭，但存在明确记录的例外（例外**不属阶段关闭条件**：事后标准变化、命名/表述、数据层已判定不阻塞；见「当前例外」清单） |

## 当前阶段

| 阶段 | 目标 | 状态 |
|---|---|---|
| P0-1 数据合同与语义规则 | 抽取数据合同（Schema V3 三级熟练度）、要求/职责/逻辑组语义规则、证据合同 | ✅ 已关闭 |
| P0-2 v0.10 结构化抽取 | 两段式（发现段 + 判断段）、证据校验、有限重试、幂等持久化 | ✅ 已关闭（真实模型验证已通过） |
| P0-3A 规则场景验证 | 领域中性场景 + 确定性变换，合同检查与变形属性检查 | ✅ 已关闭（Prompt 0.10，13 场景 hard gate=0） |
| P0-3B 真实 JD 验证 | 显式选择 JD、重复运行、合同/漂移检查、异常项索引 | 🟡 已关闭·含例外（例外 1：JD 1/2/3 历史抽取未绑定新定稿合同） |
| P0-4 要求事实归并 | instance → canonical → 唯一映射；合同校验、positive-pair Jaccard、漂移与变形检查 | 🟡 已关闭·含例外（例外 2：归并稳定性诊断指标未达标，靠人工裁决兜底） |
| P0-5 市场统计、证据追溯与 Markdown 报告 | 独立 JD 数口径统计 + 完整性门禁 + `generate-report` 离线 Markdown 报告（总览/共同/长尾/证据追溯）；真实报告私有，公开样例 `examples/market-report-sample.md` | ✅ 已关闭（样本限制声明明确） |
| P0-6 样本扩展（3 → 5 JD） | JD 4/5 抽取验收与定稿、5 JD 归并验收、3→5 增量稳定性、旧 83 条回归、最终批次与报告 | ✅ 已关闭（批次 #2：136 条/97 canonical/指纹 edfe2c1a…；旧对 0 破坏；3→5 对比摘要已生成） |
| P0-7 正式生产主线收口 | 显式数据库目标；模型候选不入正式表；app finalize 定稿；报告只消费完整定稿批次；离线来源审计；真实链路 E2E | ✅ 已关闭（2026-08-07：正式生产机制全部完成；JD 1～3 按项目级历史风险接受记录豁免，见例外 1） |
| P0-8 样本扩展（8 → 12 → 15 JD） | 先完成 JD 9～12 抽取/归并/报告，再处理 JD 13～15；逐批测量成本、稳定性与人工裁决量 | 进行中（8 JD、12 JD 均已关闭；JD13～15 extraction 已定稿，15 JD acceptance 与增量语义审核已完成；frozen-base apply 能力已就绪，下一步生成 15 JD 离线 candidate；15 为 MVP 固定终点） |

## 当前例外

| # | 所属阶段 | 例外内容 | 是否阻塞关闭 | 处置状态 |
|---|---|---|---|---|
| 1 | P0-3B / P0-7 | JD 1/2/3 正式抽取记录缺新定稿合同字段（机器分类 `unverified`，保留 legacy 人工审计结论；JD 4～15 为 `fully_bound`） | 不阻塞（2026-08-07 已批准结构化历史豁免，P0-7 关闭条件满足） | **✅ 已豁免**：`reports/P0-7/legacy-extraction-waiver.json`（批准人 project-owner，2026-08-07）。仅限 JD 1/2/3 历史记录、仅供当前 MVP 归并/统计/报告；新增 JD 禁止使用；不重新验收、不回填指纹；分类保持 `unverified`；报告 provenance 风险提示保留 |
| 2 | P0-4 / P0-8 | 归并稳定性 positive-pair Jaccard 39~67%（诊断指标）未达标，正式结果靠人工裁决兜底 | 不阻塞（P0-4 关闭时已明确判定 P0-4B 不阻塞） | 扩样 8→12→15 时逐批测量新增不稳定对与裁决量，有数据后再定是否调整 Prompt / 引入确定性归一化 |
| 3 | P0-7 | 抽取来源状态 `reviewed_unbound` 命名过乐观（八个绑定字段任一存在即标记） | 不阻塞（纯命名待办） | 非阻塞待办：细分 `fully_bound` / `reviewed_legacy` / `partially_bound` / `unverified` |

## 当前下一步

1. P0-7 已关闭（2026-08-07，豁免记录 `reports/P0-7/legacy-extraction-waiver.json`）；
   例外 1 处置完成，JD 1～3 不回填、不重验、不宣称 `fully_bound`；
2. 进入扩样最终阶段（固定终点 15 JD）：8 JD 与 12 JD 批次均已关闭；JD13～15
   extraction 已按批准 run 0/0/1 定稿为 31/64/14 requirements，新增 109、
   15 JD 合计 409 instances，来源均 `fully_bound`；15 JD consolidation
   acceptance 与增量语义审核已经完成；
3. frozen-base 离线 apply 已能直接继承正式 12 JD final result（IDs 1～300、
   241 canonical / 300 mappings、结果指纹 `47591259…e052f`），并只允许
   IDs 301～409 的增量裁决。下一步生成 15 JD review-decisions 与离线 candidate；
   order transformation hard gate=1 仍是 finalize 前的独立 blocker；
4. 新增 JD 必须全部走现行正式主线，禁止使用例外 1 豁免。

边界与硬依赖见 `AGENTS.md`（不维护旧版本兼容、付费调用必须
`--execute`、私有材料不提交 Git、MVP 轻量开发原则）。
