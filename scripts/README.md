# scripts 目录说明

该目录保存开发期实验、数据迁移或维护脚本。正式业务能力和公共命令位于`app/`，本目录不作为正式业务入口。

## 目录职责

| 路径 | 职责 |
|---|---|
| `__init__.py` | 标记维护脚本包，导入时不执行操作。 |
| `experiments/` | 按P0功能点保存可复现的实验与临时验收编排脚本。 |

所有Python脚本均应从项目根目录以模块方式运行，例如`python -m scripts.experiments.p0_3.run_two_stage_extraction`。
