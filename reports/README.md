# 实验报告

本目录按 P0 功能单元保存详细实验分析（`reports/P0-X/`）。P0 工作文档（`docs/work/P0-X.md`）只记录关键指标、结论和报告入口，不复制长日志或逐案结果。

## 目录说明

- `reports/P0-3/`：P0-3 两段式抽取实验与验收报告（`two-stage-experiment.md`、`root-cause-analysis.md`、`validation-acceptance.md` 等；`validation-draft-baseline.md` 为冻结前草案，已标记过期）。
- `reports/P0-4/`：P0-4 归并实验报告与脱敏预检指标（`consolidation-prompt-analysis.md`、`precheck-2.1.json` 等）。
- `reports/experiments/`：实验脚本生成的本地评测草稿。

## 隐私边界

详细报告可能包含困难样例、真实语义细节或可还原 JD 的信息，因此 `reports/*` 默认被 Git 忽略，仅本 README 受跟踪。只有完成隐私审计、确认不含真实 JD、完整证据、人工标准答案、原始模型响应或个人信息后，才可单独调整跟踪范围。

原始模型响应、真实数据实验输出和完整失败材料应放在 `data/private/experiments/P0-X/` 等私有位置，而不是本目录。
