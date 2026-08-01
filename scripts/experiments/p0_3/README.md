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

# 离线评测，不产生模型费用
python -m scripts.experiments.p0_3.evaluate_two_stage_results
```

默认原始结果写入`data/private/experiments/p0_3/`，默认评测报告写入`reports/experiments/p0_3/`。
