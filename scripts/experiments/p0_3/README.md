# P0-3 两段式实验脚本

本目录保存两段式抽取的真实调用和离线评测入口；两段式实现本身位于`app/extraction_two_stage.py`。

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | 标记P0-3实验包，导入时不执行操作。 |
| `run_two_stage_extraction.py` | 显式选择数据库并调用真实LLM，把原始结果写入私有实验目录。 |
| `evaluate_two_stage_results.py` | 离线读取私有标注与实验结果，生成分层指标报告，不调用LLM。 |

## 运行方式

```powershell
# 使用项目数据库执行真实调用；必须显式确认execute
python -m scripts.experiments.p0_3.run_two_stage_extraction `
  --use-project-database --execute

# 只处理单份JD（可重复指定），用于单份先验证
python -m scripts.experiments.p0_3.run_two_stage_extraction `
  --use-project-database --job-id 1 --execute

# 离线评测，不产生模型费用
python -m scripts.experiments.p0_3.evaluate_two_stage_results
```

- 参数：`--use-project-database` 与 `--database-url` 必须二选一；`--job-id` 可重复、缺省为全部JD；真实调用必须 `--execute`。
- 默认原始结果写入`data/private/experiments/p0_3/`，脚本当前生成的本地评测草稿写入`reports/experiments/p0_3/`。
- 脱敏后的P0-3详细分析统一整理到`reports/P0-3/`；当前两段式实验主报告为`reports/P0-3/two-stage-experiment.md`。
- 实验版本号位于`app/extraction_two_stage.py`的`TWO_STAGE_PROMPT_VERSION`（当前v0.6）。
