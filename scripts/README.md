# scripts 目录说明

正式数据语义、candidate 生成、finalize、审计和报告入口位于 `app/`。本目录只保留
v0.1 当前验收/裁决编排与公开 sample 生成器。

| 脚本 | 职责 | 数据库与付费边界 |
|---|---|---|
| `experiments/p0_3/run_acceptance.py` | 规则场景与确定性变形验收 | 无数据库；真实模型运行需 `--execute` |
| `experiments/p0_3/run_real_jd_acceptance.py` | 真实 JD 多次抽取验收 | 必选 `--use-project-database` 或 `--database-url`，必选范围；付费运行需 `--execute`，raw 写私有目录 |
| `experiments/p0_4/run_acceptance.py` | 归并多次运行、顺序变形与验收报告 | 必填 `--database-url`；付费运行需 `--execute` 和 `--raw-output`；不传 `--job-ids` 即全部 |
| `experiments/p0_4/analyze_stability.py` | 离线生成稳定性与人工审核材料 | 必填 `--database-url`、raw/report/analysis 输出；不调用模型 |
| `experiments/p0_4/apply_review_decisions.py` | 离线应用 must-link、cannot-link、名称 override 与 frozen base | 必填 `--database-url`、审核输入与输出；不调用模型 |
| `make_sample_report.py` | 用虚构数据生成公开 Markdown sample | 临时 SQLite；不调用模型、不读取私有数据 |

P0-4 脚本只接受显式数据库 URL，实验时优先使用正式数据库的临时副本。任何含完整 JD、
evidence 或模型响应的 raw 产物必须写入 Git 忽略的私有路径。
