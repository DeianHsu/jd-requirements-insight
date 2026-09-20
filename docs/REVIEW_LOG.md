# 评审日志（Review Log）

## 最近一轮：归并改为 JSON Schema 结构化生成（2026-09-21）

- 删除上一轮对错误 `source_requirement_id` 的返回后别名兼容，不再接受或修补
  任何非正式字段。正式合同只有必填、非空的 `source_requirement_ids`。
- 归并客户端从 Chat Completions `json_object` 切换到 DeepSeek Responses API
  `json_schema`，生成阶段直接使用与本地 Pydantic 复验同源的完整响应合同。
  未知字段、缺失字段、空来源和类型错误由生成 Schema 阻止。
- 本地仍在返回后执行同一 Pydantic 合同，并保留精确 ID 覆盖、唯一分区、
  未知 ID 与确定性 mapping 等跨项语义硬门；归并 Prompt/执行身份升级为 4.5。
- OpenAI SDK 最低版本调整为 2.0 并更新锁文件；同步架构、当前状态和
  验证合同。本轮未调用付费模型，未修改私有 JD 或正式数据库。
- 验证：项目 `.venv` 执行全量测试，346 项全部通过；
  `uv run ruff check app scripts tests` 通过。`uv run pytest` 仍因当前 Windows 的 uv
  trampoline 路径标准化失败，已用同一项目虚拟环境完成等价全量验证。
- 用户已有未跟踪 `.idea/` 保持原状。
