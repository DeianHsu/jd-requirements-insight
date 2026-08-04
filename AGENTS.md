# JD Skill Insight 仓库约束

## 当前唯一主线

```text
JD 导入
→ v0.8 + Schema V3 结构化抽取
→ 抽取质量验证
→ requirement instance 归并为 canonical requirement
→ 独立 JD 统计
→ 原文证据追溯
→ Markdown 市场分析报告
```

不维护已淘汰、冻结或不服务当前 MVP 的功能；不维护旧方案与历史兼容
（旧抽取版本、旧 Schema、旧数据库结构、层级关系）。历史由 Git 保存。

## 新会话推荐阅读顺序

```text
AGENTS.md
→ README.md
→ docs/PROJECT_PLAN.md
→ docs/ARCHITECTURE.md
→ docs/CURRENT_STATE.md
→ 根据任务读取 GLOSSARY、annotation 或对应代码
```

## 事实优先级

1. 当前代码、测试、数据结构和 Git 状态；
2. `docs/CURRENT_STATE.md` 与 `docs/PROJECT_PLAN.md` 的当前状态；
3. 聊天记忆只作辅助，不得覆盖正式文件。

## 变更约束

1. 不得静默改变公共接口、Schema、数据语义、功能范围；重要变化更新
   对应文档（PROJECT_PLAN / ARCHITECTURE / GLOSSARY / annotation）。
2. 不得覆盖、删除或重置用户已有改动；提交时使用路径级暂存。
3. 真实 JD、数据库、密钥和原始模型响应不得提交；私有材料只存在于
   `data/private/`、`data/raw_jds/` 与本地数据库。
4. 文档只保存当前有效事实，不记录迭代过程；不新增 DEC/历史归档来
   记录清理，不用 deprecated/legacy/experimental 标记代替删除。

## MVP 轻量开发原则

1. 当前以完成可演示、可评测的 MVP 闭环为最高优先级。
2. 默认采用满足当前需求的最小实现，不提前建设通用框架。
3. 一次任务可以处理一组紧密相关的问题，但不得扩展到无直接关系的邻近问题。
4. 只有真实数据、现有测试或明确需求证明必要时，才新增抽象、兼容逻辑、配置、脚本或验证规则。
5. 优先复用、简化和删除，不为未来可能需求增加当前复杂度。
6. 测试只覆盖核心合同、真实缺陷和关键回归，不做排列组合式穷举。
7. 文档只记录当前事实、关键边界和下一步，历史过程交给 Git。
8. 发现非阻塞问题时只记录，不默认顺手修复。
9. 修改范围明显超出原任务时，停止扩展并回到最小闭环。

不削弱安全、隐私、版本门禁和付费调用保护规则。

## 验证与实验

1. 修改后运行：`uv run pytest`（系统 Temp 被锁定时加 `--basetemp .pytest-tmp`）
   与 `uv run ruff check app scripts tests`；
2. 自动化测试不得调用付费外部服务；测试使用临时文件和临时数据库；
3. 付费模型调用必须显式 `--execute` 确认（extract-jds /
   consolidate-requirements / 验证脚本均如此），并先说明模型、数据范围
   和目标；数据库操作必须显式选择目标，不得隐式写入项目数据库；
4. 遇到旧抽取数据或旧数据库结构：不兼容、不迁移、不自动删除，给出
   提示"备份原始 JD，删除旧派生数据库并重新生成"。

## Git 提交与推送

- 可以自行 commit：只暂存本任务相关文件，填写清晰的 Summary/Description
  （修改内容、原因、验证、剩余风险），每次提交内部结构一致；
- **严格禁止 push**，也不得改写已有 Git 历史、amend 用户提交或删除
  用户提交；所有 push 由用户手动执行。
