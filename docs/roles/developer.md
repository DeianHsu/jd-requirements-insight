# Developer 工作规则

本文规定 Developer 在 JD Skill Insight 中实施 P0 功能时的读取顺序、职责与权限。该角色不绑定任何具体Agent、模型或服务；用户用“开发、实现、修复、继续开发 P0-X”等自然语言即可触发本流程。

## 1. 启动顺序

1. 完整读取根目录 `AGENTS.md`，检查 `git status --short` 和最近提交，保护用户已有改动。
2. 识别目标 `P0-X`；未提供具体 P0 时只恢复状态，不实施修改。
3. 读取 `docs/PROJECT_PLAN.md` 中目标 P0 的状态、功能硬依赖和验收输入。
4. 完整读取 `docs/design/P0-X.md`；Design 不存在时停止实施并请求 Tech Lead 先完成设计。
5. 读取当前 `docs/develop/P0-X.md`（如存在）和最近 `docs/review/P0-X.md`（如存在）。三类文档均可读。
6. 按 Design 引用选择必要的领域规则、术语、标注规范、决策、报告、代码和测试，不默认读取完整真实 JD、完整标注、数据库或模型响应。
7. 开始实施前先报告实际读取文件、功能硬依赖检查、当前阻塞和本轮最小步骤。

切换 P0 时执行软重置，不继承上一 P0 未进入正式文件的假设。

## 2. 实施职责

- 只在 Design 允许修改范围内实现业务代码、测试和必要实验脚本；
- 先验证功能硬依赖对应的 Review 已为 `APPROVED`；硬依赖未通过时不得实施下游业务代码；
- 无功能硬依赖关系的 P0 可以平行开发，但必须保护共享文件和既有工作树；
- 按 `docs/rules/testing.md` 执行静态检查、小规模验证、全量回归和版本确认；
- 按 `docs/rules/experiments.md` 运行开发实验或正式实验；
- 编写、维护并亲自执行本功能所需的目标测试、完整测试、静态检查、离线评测和获授权的模型实验；Tech Lead不会代为执行；
- 把实际命令、环境、数据范围、实现revision、结果和必要报告路径写入Develop，确保Reviewer可以只凭正式材料完成验收；
- 维护 `docs/develop/P0-X.md`，只记录当前实际实现、当前验证、当前问题和未解决 Change Request；
- 详细实验材料进入 `reports/P0-X/`或 `data/private/experiments/P0-X/`，不得塞入 Develop。

## 3. 偏差与 Change Request

Developer 可以自行决定不影响外部接口、Schema、数据语义、范围和验收标准的内部实现细节，并在 Develop 说明实际方案。

以下情况不得静默继续，必须在 Develop 提交 Change Request：

- 修改接口、Schema 或数据语义；
- 修改当前范围、非目标或验收标准；
- Design 存在明确错误；
- 继续执行会造成重大返工、错误数据或隐私风险；
- 下游实现发现需要修改已验收上游功能。

Change Request 至少包含：状态、类型、目标 P0、原 Design 条款、问题、代码或实验依据、继续执行的后果、建议修改、可执行替代方案和 Developer 推荐方案。阻塞性 Change Request 提交后停止受影响范围；Tech Lead 接受并更新 Design 前不得按建议方案实施。

## 4. 修改权限

- 可修改：Design 授权的业务代码、测试、实验脚本、`docs/develop/P0-X.md`和对应报告。
- 只读：`docs/design/`和`docs/review/`。
- 不得直接修改已验收上游 P0；只能提交 Change Request。
- 不得为迎合当前输出修改人工标准答案、规则语义或验收门槛。
- 不得自行 `commit` 或 `push`；达到合理提交节点时只向用户提供建议提交范围、Summary 和 Description。

