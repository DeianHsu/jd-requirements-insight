# 评审日志（Review Log）

最近一次任务的执行摘要，供外部 Reviewer（如 ChatGPT）读取。每次任务完成时覆盖更新，
只保留最新一轮；历史由 Git 保存。项目状态以 `docs/CURRENT_STATE.md` /
`docs/PROJECT_PLAN.md` 为准。

## 最近一轮：v0.1 MVP portfolio package（2026-08-14）

- 从首次访问仓库的招聘方视角重排 README：先说明项目价值和 v0.1 MVP 结果，再展示
  完整 pipeline、工程亮点、公开 sample、正式入口、评测/回归、仓库结构、隐私边界与
  已知限制；明确 15 JD / 409 instances / 329 canonical、43 个跨 JD 共同要求和
  286 个长尾要求均来自已关闭批次，不能外推为行业结论。
- README 突出候选/正式数据隔离、evidence 追溯、fingerprint、人工裁决、metamorphic /
  stability 验收、frozen baseline、原子 finalize 与幂等复跑等工程闭环；所有公开文档
  入口改为可点击相对链接，旧固定 consolidation ID 改为通用入口说明。
- 公开 `examples/market-report-sample.md` 使用虚构公司和岗位，通过项目现有生成器在临时
  SQLite 中复现；重新生成 SHA-256 与仓库版本一致，不调用 LLM、不读取私有数据。
- 修正公开状态中的正式归并批次数量（5 份）、当前可报告批次（#5）、P0-8 Jaccard
  最低观察值，以及 annotation 中指向不存在文件的说明。
- 验证：Markdown 相对链接全部存在；README/docs/examples 未发现真实公司名；CLI help
  可运行；`git diff --check` 通过。仅修改文档，按要求未跑全量 pytest；未修改算法、
  正式数据或 sample 内容，未调用 LLM，未创建 v0.1 tag。当前无 portfolio blocker。
