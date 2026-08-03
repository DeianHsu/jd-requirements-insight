# experiments 目录说明

该目录只保存阶段实验和临时验收的编排代码，不保存可复用业务逻辑、私有数据或原始模型响应。可复用逻辑必须进入 `app/` 并配套测试。

## 当前有效实验脚本

| 路径 | 职责 | 是否付费 |
|---|---|---|
| `p0_3/run_two_stage_extraction.py` | P0-3 两段式抽取真实调用（发现段+判断段），结果写入私有目录 | 是（必须 `--execute`） |
| `p0_3/evaluate_two_stage_results.py` | P0-3 离线分层评测（development/regression/validation 三分组，支持指定验收轮次结果文件） | 否 |
| `p0_4/run_small_scale_precheck.py` | P0-4 小规模预检（标准项/分块映射/关系三阶段，无人工 Gold 依赖），结果脱敏后写入 `reports/P0-4/` | 是（必须 `--execute`） |
| `p0_4/run_acceptance.py` | P0-4 真实模型验收：3 次独立运行稳定性 + 顺序/分块变形测试 + 合同/稀疏度/下游不变性 + 机器可读验收报告（hard gate/warning/diagnostic 分级） | 是（必须 `--execute`） |

运行方式与参数详见各子目录 README（`p0_3/README.md`、`p0_4/`）。

## 输入与输出位置

- 原始模型响应、真实 JD 原文、完整实验输出：`data/private/experiments/P0-X/`（Git 忽略，不入库）。
- 脱敏后的阶段指标与请求记录摘要：`reports/P0-X/`（默认 Git 忽略，仅 `reports/README.md` 受跟踪）。
- 评测脚本生成的本地草稿：`reports/experiments/`。

## 付费调用与数据库保护

- 所有真实 LLM 调用必须显式确认（`--execute`），运行前说明模型、数据范围和目标；没有 `--execute` 时脚本只打印计划，不发起调用。
- 数据库目标必须显式选择：`--use-project-database`（项目数据库）与 `--database-url`（其他数据库）二选一，不得隐式写入项目数据库。
- 自动化测试不得调用付费外部服务；导入实验脚本不得读取配置、连接数据库、写文件或调用外部服务。

## 私有输出边界

- 受 Git 跟踪的实验脚本不得内嵌真实 JD 句子、人工标准答案、判断说明或原始模型响应；需要这些材料时只能从 `data/private/` 读取，并在输出中保持脱敏。
- 含困难样例、真实语义细节或可还原 JD 内容的报告继续被 Git 忽略。
