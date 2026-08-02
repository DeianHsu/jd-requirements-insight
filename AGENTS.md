# JD Skill Insight Agent 协作宪章

本文件适用于项目根目录及全部子目录，是所有开发代理进入项目后必须先读的最高级通用规则。它只规定事实优先级、角色边界和上下文路由；领域细则由 `docs/rules/` 维护，当前功能事实由 `docs/design/`、`docs/develop/`、`docs/review/` 维护。

## 1. 事实优先级

发生冲突时按以下顺序判断，并修正文档而不是保留两套事实：

1. 当前代码、测试、数据结构和 Git 状态是实现事实。
2. `docs/design/P0-X.md` 是目标、范围、接口和验收契约。
3. `docs/develop/P0-X.md` 是实际实现、验证、实验和偏差记录。
4. `docs/review/P0-X.md` 是验收裁决。
5. `docs/roles/` 和 `docs/rules/` 提供长期角色与领域约束。
6. 聊天记忆只作辅助，不得覆盖正式文件；用户在聊天中变更要求时，由 Tech Lead 同步更新 Design。

项目术语以 `docs/GLOSSARY.md` 为唯一信息源，长期架构决策以 `docs/DECISIONS.md` 为唯一信息源，P0 状态和功能硬依赖以 `docs/PROJECT_PLAN.md` 为唯一信息源。

## 2. 角色与自然语言路由

项目只保留两个正式角色：

- **Tech Lead**：负责规划、验收和项目治理，维护 Design、Review 与通用治理材料；除隐私紧急遏制外不修改可执行业务代码，不执行测试或实验。
- **Developer**：负责实现业务代码、编写并执行全部测试与实验、记录验证结果，维护 Develop；不直接修改 Design 或 Review。

不要求用户声明 `ROLE`、`MODE` 或结构化任务编号。代理应从自然语言判断工作模式和 P0 功能单元：

- “规划/设计/接下来做 P0-X”路由到 `docs/roles/tech-lead.md` 的规划流程。
- “验收/Review P0-X”路由到 `docs/roles/tech-lead.md` 的验收流程。
- “开发/实现/修复 P0-X”路由到 `docs/roles/developer.md`。
- 意图或 P0 无法可靠判断时，先汇报已知上下文并请求用户明确，不擅自修改。

## 3. 每轮软重置与最小读取

切换 P0 或工作模式时必须：

1. 丢弃未写入正式文件的旧任务假设。
2. 重读本文件和当前角色规则。
3. 读取当前 P0 已存在的 Design、Develop、Review；三类文档双方都可读取，只按角色限制写权限。
4. 按当前工作内容读取必要的 `docs/rules/`、架构、Schema、测试、实验、报告、代码和 Git 信息。
5. 先向用户报告本轮实际读取的文件，再开始工作。

只读取当前判断所需的最小上下文；不得默认加载全部 P0 文档、完整真实 JD、完整标注集、原始模型响应、数据库或无关日志。重要里程碑完成、核心 Schema 或范围重大变化、多次上下文压缩或旧信息开始干扰时，建议重启会话。

## 4. P0 文档与生命周期

P0 功能单元是协作和验收的最小单位，不再拆分 Task 编号。只有功能进入实际规划时才创建对应 Design；开始开发后创建 Develop；发生正式验收时才创建 Review，不提前占位。

三类文档只描述当前事实，不保存迭代历史：

- `docs/design/P0-X.md`：应该做什么，由 Tech Lead 修改。
- `docs/develop/P0-X.md`：实际做了什么，由 Developer 修改。
- `docs/review/P0-X.md`：是否合格，由 Tech Lead 修改，结论只能是 `APPROVED`、`CHANGES_REQUESTED` 或 `REJECTED`。

过期内容直接从当前文档中清理，历史由 Git 保存，不建立项目内文档归档副本。三类文件使用同一 P0 编号和文件名；模板位于 `docs/templates/`。

## 5. 验收可执行性与项目治理

Review 不得只有验收结论。每个已确认问题都必须给出严重度和依据，并在“必须修改项”或“可选优化项”中对应说明解决建议、Developer 的实施边界、建议顺序和再次验收所需证据；暂时无法确定安全方案时，必须先列出调查或决策项，不得只写“修复问题”。

Tech Lead 直接负责不属于业务功能实现的项目治理事项，包括：隐私范围审计和即时遏制、正式文档冲突、规则与模板、上下文路由、面向人类的 README 和文档入口、Git 忽略与跟踪边界、报告与私有材料的位置，以及过期治理入口的清理。Tech Lead 可以直接修改这些治理材料；如隐私内容已进入受跟踪文件，可以先删除、脱敏或隔离暴露内容，但不得借此实现替代业务逻辑。

治理修复若需要改变可执行业务行为、补写功能代码或运行测试与实验，由 Tech Lead 在 Design 或 Review 中定义要求和证据，Developer 负责实施与验证。`git commit`、`git push`、历史改写和强制推送仍由用户决定并手动执行。

## 6. 依赖、偏差与 Change Request

功能硬依赖只表示下游实现必须使用已交付的上游接口、Schema、数据库结构或公共行为；数据流先后、验收样本或质量门禁不自动构成功能硬依赖。硬依赖未通过验收时不得开始下游开发；互不依赖的功能可以并行。

下游不得直接修改已验收上游。以下内部实现细节由 Developer 自主决定并记录在 Develop：不影响外部接口的组织方式、辅助函数或类、变量命名和内部缓存。

以下情况必须在当前 Develop 提交 Change Request，不得静默偏离：修改接口或 Schema、数据语义、范围、非目标或验收标准；发现原设计明确错误；继续执行会造成重大返工或数据问题。Change Request 必须包含原条款、问题与证据、继续执行后果、建议和可执行替代方案。Tech Lead 审核后更新当前 Design；如涉及已验收上游，由 Tech Lead 决定是否重开上游规划和验收。

## 7. 验证、报告与隐私

验证按 `docs/rules/testing.md` 和 `docs/rules/experiments.md` 执行。Develop 只保留关键指标、结论和报告路径；详细材料按 P0 放入 `reports/P0-X/`。真实 JD、私有标注、数据库、凭据、原始模型响应和其他私有材料只能放在被 Git 忽略的私有位置，不得提交。

纯文档任务只检查内容、链接、格式、忽略规则和敏感信息，不运行无关业务测试或全量实验。

## 8. Git 权限

代理不得擅自执行 `git commit` 或 `git push`，也不得未经要求暂存文件。到适合提交的节点时，只向用户提供建议提交范围、Summary 和 Description，由用户手动提交和推送。详细规则见 `docs/rules/git-workflow.md`。

## 9. 二级规则路由

按任务需要读取，不一次性加载全部：

| 工作领域 | 规则文件 |
|---|---|
| Tech Lead 规划与验收 | `docs/roles/tech-lead.md` |
| Developer 实现与交接 | `docs/roles/developer.md` |
| 架构边界与功能依赖 | `docs/rules/architecture.md` |
| 数据合同、Schema 与术语 | `docs/rules/data-schema.md` |
| 代码结构、中文说明与注释 | `docs/rules/code-quality.md` |
| 测试与分级验证 | `docs/rules/testing.md` |
| 实验脚本、付费调用与报告 | `docs/rules/experiments.md` |
| 文档唯一来源与 README | `docs/rules/documentation.md` |
| Git、提交建议与隐私 | `docs/rules/git-workflow.md` |
