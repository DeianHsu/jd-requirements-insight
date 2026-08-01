# P0-11 演示与项目文档

```yaml
p0_id: "P0-11"
plan_item: "演示与项目文档"
status: "partial"
baseline_commit: "e1a89f1"
verified_revision: "development fixes based on e1a89f1"
related_decisions: ["DEC-002", "DEC-003", "DEC-006"]
glossary_terms: ["P0-N", "开发中", "部分完成", "公共CLI"]
depends_on: ["P0-1", "P0-2", "P0-3", "P0-4", "P0-5", "P0-6", "P0-7", "P0-8", "P0-9", "P0-10"]
affects: []
dependency_mode: "selective"
```

# 功能目标与边界

确保README、架构说明、运行步骤和演示脚本与实现一致，使项目核心链路能够被新开发者恢复并被公开用户复现。P0-11不扩展复杂前端或完整业务API。

# 当前状态

部分完成。根README、功能目录README、术语词典、标注规范、项目路线图、决策记录、上下文路由、发布规则和P0 handoff体系已经建立；架构图和完整演示脚本尚未完成。

# 稳定事实

- 根README面向公开用户；功能目录README说明代码职责和数据流。
- `GLOSSARY.md`是术语唯一信息源，`PROJECT_PLAN.md`是范围和状态唯一信息源。
- 每个P0功能点固定对应一份持续维护的handoff。
- 文档不得复制真实JD、完整模型JSON、完整人工标准答案、数据库内容或密钥。
- commit和push由用户手动执行。

# 实现与文件入口

- `README.md`、`app/README.md`、`tests/README.md`：公开与目录入口。
- `docs/GLOSSARY.md`、`DOCUMENT_RULES.md`、`PROJECT_PLAN.md`、`DECISIONS.md`：治理文档。
- `docs/CONTEXT_ROUTING.md`：本地最小上下文路由。
- `docs/handoff/README.md`及P0 handoff：会话恢复入口。
- `docs/PUBLISH_RULES.md`：公开边界。

# 数据合同与不变量

- 文档中的代码标识必须与实际文件、类、枚举和CLI一致。
- 未提交草稿标记为“🟠 开发中”，不得当作稳定事实。
- 相对链接必须有效；新增或重命名代码文件时同步目录README。
- 项目路线图不使用日历期限，只维护短期、近期和长期范围。

# 测试与验证

当前工作树110项测试和Ruff通过；根目录临时Python脚本已清理，实验入口迁入`scripts/experiments/p0_3/`，P0-4离线评测进入正式CLI。AGENTS、文档规则、词典、README及受影响handoff已同步实验性脚本和临时验收规则。

# 未完成事项与已知问题

- 架构图尚未建立。
- 完整演示脚本需等P0核心链路稳定后编写。
- `PROJECT_PLAN.md`、`DECISIONS.md`、`CONTEXT_ROUTING.md`和`reports/`当前被Git忽略，本地更新不会自动进入普通提交。

# 继续开发入口

文档任务先读取`docs/DOCUMENT_RULES.md`和目标文档；新会话接手具体功能时读取`AGENTS.md`、目标P0计划行和对应handoff，不默认加载全部handoff。
