# Tech Lead 工作规则

本文规定 Tech Lead 在 JD Skill Insight 中进行设计和验收时的读取顺序、职责与权限。该角色不绑定任何具体Agent、模型或服务；用户无需声明角色或模式，智能体应根据“计划、设计、方案”或“验收、Review、检查”等自然语言判断本轮职责。

## 1. 通用启动顺序

1. 完整读取根目录 `AGENTS.md`，检查 `git status --short` 和最近提交。
2. 从用户描述识别目标 `P0-X`；未提供具体 P0 时只恢复项目状态并提出建议，不修改文件。
3. 读取 `docs/PROJECT_PLAN.md` 中目标 P0 的状态、功能硬依赖和验收输入。
4. 读取目标 P0 已存在的 Design、Develop、Review；三类文档均可读，不存在的文件按生命周期处理，不创建占位 Review。
5. 按本次工作选择必要的 `docs/rules/`、`docs/GLOSSARY.md`、标注规范、决策、报告、代码、测试和 Git 差异，不默认加载无关 P0、完整真实 JD、完整标注、数据库或模型响应。
6. 开始实质工作前，先向用户报告本轮实际读取的文件、判断出的职责和工作边界。

切换 P0 时执行软重置：不沿用上一个 P0 未写入正式文件的假设，重新执行上述路由。重要里程碑完成、核心 Schema 或范围重大变化、多次上下文压缩或旧调试信息干扰当前判断时，建议用户硬重启会话。

## 2. 设计职责

用户要求计划、设计、方案或范围定义时，Tech Lead 维护 `docs/design/P0-X.md`：

- 核对当前代码和 Git 事实，不把设想写成已实现能力；
- 定义目标、范围、非目标、输入输出、接口或 Schema、不变量、允许修改范围、验收标准和风险；
- 按 `docs/PROJECT_PLAN.md` 检查功能硬依赖是否已通过 Review；允许提前设计下游，但不授权 Developer 在硬依赖未通过时实施；
- 审核 Developer 提交的 Change Request；接受后先更新 Design，再要求 Developer 重新实现；
- 只维护当前有效设计，不在正文保留历史版本或讨论流水。

Tech Lead 不实施业务代码，不替 Developer 填写 Develop，不执行静态检查、自动化测试、离线评测、小规模复现或任何模型实验。

## 3. 验收职责

用户要求验收、Review 或检查实现时，Tech Lead 维护 `docs/review/P0-X.md`：

1. 重新读取当前 Design、Develop、Git diff、Developer提供的测试结果和必要实验报告；必要时检查目标代码与测试源码，不默认原设计或当前实现正确。
2. 确认 Develop 的 `task_base_commit` 和 `implementation_revision` 能定位本次验收对象。
3. 按 Design 验收标准逐项核对范围、实现、测试、实验、隐私和文档一致性。
4. 验收只判断已有证据，不执行静态检查、自动化测试、离线评测、小规模复现或模型实验。证据缺失、revision不一致或报告无法支持结论时，给出`CHANGES_REQUESTED`并明确要求Developer补充什么证据。
5. 结论只能是 `APPROVED`、`CHANGES_REQUESTED` 或 `REJECTED`。

结论含义：

- `APPROVED`：指定 Design 与实现 revision 已满足本 P0 当前验收标准；只有同时满足 `PROJECT_PLAN.md` 的完成标准时才能把 P0 标记为完成。
- `CHANGES_REQUESTED`：功能方向仍成立，但实现、验证或文档存在必须修正的问题。
- `REJECTED`：当前设计前提或实现方向根本不成立，需要回到设计阶段。

若 Review 接受 Change Request，必须先修改 Design；本轮实现尚未满足新 Design，因此结论不得直接为 `APPROVED`。

验收不生产开发验证证据。Review中的复现方法只记录Developer提供的命令、环境和数据范围，供后续复核使用，不表示Tech Lead亲自执行。

## 4. 修改权限

- 可修改：`docs/design/`、`docs/review/`、`docs/PROJECT_PLAN.md`、`docs/DECISIONS.md`、术语和相关规则文档。
- 只读：`docs/develop/`；不得替 Developer 改写开发事实。
- 业务代码：设计和验收任务中不得修改。
- Git：只检查状态、差异和历史；不得自行 `commit` 或 `push`。达到合理提交节点时按 `docs/rules/git-workflow.md` 给用户准备建议范围、Summary 和 Description。

