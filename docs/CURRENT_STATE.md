# 当前状态

updated_at: 2026-08-04
implementation_baseline: 5c3255e（仓库收缩完成；Git 是当前 HEAD 的唯一事实来源）

## 当前可运行功能

| 命令 | 功能 |
|---|---|
| `python -m app.cli import-jds <目录>` | 导入 Markdown JD（frontmatter + 正文） |
| `python -m app.cli list-jds` | 列出 JD 摘要 |
| `python -m app.cli extract-jds [--all|--job-id N] --execute` | v0.8 + Schema V3 两段式抽取（付费，需 .env 配置与 --execute） |
| `python -m app.cli list-extractions` | 列出抽取结果 |
| `python -m app.cli consolidate-requirements --all|--job-id N --execute` | 跨 JD 归并为 canonical requirement（付费，需 --execute） |
| `python -m app.cli list-consolidations` | 列出归并批次 |
| `python -m app.cli validate-consolidation --consolidation-id N` | 离线验证（不付费；回查批次真实输入，失败返回非零） |
| `app/market_analysis.py` | 市场统计（实例数、独立 JD 数、importance 分布、来源证据、稳定排序），供下一阶段 `generate-report` 消费 |

验证脚本（均需 `--execute` 才调用付费模型，`--dry-run` 预检不付费）：

- P0-3A 规则场景：`python -m scripts.experiments.p0_3.run_acceptance --execute`
- P0-3B 真实 JD：`python -m scripts.experiments.p0_3.run_real_jd_acceptance --use-project-database --all --execute`
- P0-4 归并验收：`python -m scripts.experiments.p0_4.run_acceptance --execute`
  （缺省自动选择所选 JD 的唯一共同 v0.8 + Schema V3 抽取版本；查询前
  验证数据库结构；顺序变形合同违规计入 hard gate）
- P0-4 小规模预检：`python -m scripts.experiments.p0_4.run_small_scale_precheck --execute`

## 当前数据规模（当前维护环境，本地私有，不入库提交）

最近一次真实数据准备（2026-08-04，无付费调用）后：

- 当前数据库已使用现行 Schema 创建（六张业务表，无旧表）；
- 5 份真实 JD 已重新导入 `data/jd_skill_insight.db`（重复导入幂等跳过）；
- 当前尚无 v0.8 抽取结果；
- 当前没有归并批次；
- 真实 JD 原文属于私有输入（Git 忽略；重新克隆仓库的环境不会包含
  这些私有文件）。

## 已完成的真实验证准备（均无付费调用）

- P0-3A dry-run 通过：13 个规则场景锚点计划输出，返回成功；
- P0-3B dry-run 通过：单份（ID 1）与三份（ID 1/2/3）均输出输入
  fingerprint 与运行计划，返回成功；
- `extract-jds` 计划模式正确：显示模型名（deepseek-v4-flash）、
  v0.8 + Schema V3、选中 JD 数与 --execute 提示，不初始化客户端、
  不产生抽取记录；
- 全量自动化测试与 Ruff 通过。

## P0-3A 验证器定点修复与离线重算（2026-08-04，无付费调用）

原始真实运行结果保留不覆盖：`reports/P0-3/acceptance-20260804-000314-report.json`
（hard_gate_failures=7，warnings=1）。

验证器修复（本轮完成）：

- 新增 `evidence_pairing_key()`：NFKC + 空白规范化后只移除开头排版型
  列表标记（数字+点/顿号、括号数字、中文序号+顿号、短横线/圆点项目
  符号），保留正文全部语义数字（`3年` vs `5年`、`Python 3` 不受影响）；
- `_pair_items` 分组键、`_group_ids_of` 组身份、`new_condition_items`
  判定改用配对键；`new_condition_items` 改为按配对结果（对象身份）
  判定，拆句场景不再误报新增；
- fallback 兜底增加语义数字集合兼容检查（3年/5年不得兜底配对）与
  名称包含规则（文本整体替换但条件名保留时可配对）；
- 变形块对齐允许一个 variant 块对应多个 base 块（发现段块粒度不同
  不再静默丢项）；未配对统计改为全局对象身份去重；
- `group_members_preserved` 的 any_of 组检查改为与块内 any_of 项数
  比较（块内可混合 standalone）；
- `group_change_anchor` 识别为元数据键不再产生未知属性 warning，
  真正未知属性仍 warning；
- `resolve_property_anchors` 补充解析 `importance_expected_change`
  的 anchor（此前漏解析导致检查永远失败）。

离线重算（同一批模型响应，未调用模型）：
`reports/P0-3/acceptance-20260804-000314-revalidated.json`

- 原 hard gate：7 → **新 hard gate：4**；原 warning：1 → 新 warning：0；
- 消除的假阳性：SCN-003 no_new_conditions、SCN-006 fact_set/
  importance_expected_change、SCN-007 group_members_preserved ×2、
  SCN-009 no_new_conditions（拆句）、SCN-010 no_new_conditions、
  group_change_anchor warning；
- 剩余 4 个失败全部为真实问题：
  - SCN-006 group_type/group_membership：模型把“有技术甲和框架乙
    相关项目经验者优先”（“和”关系）建为 any_of 组（真实 GROUP 错误）；
  - SCN-006 proficiency：模型按规则把“相关项目经验”判 unknown，
    与场景期望（proficiency 不变）矛盾——场景期望与 Schema V3 规则
    （项目经验→unknown，见 SCN-013）不一致；
  - SCN-007/SCN-008 category：占位词（框架乙/技术丙）类别语义不明，
    category 判定在改名/同词时漂移——场景歧义 + 模型字段稳定性并存。

结论：验证器假阳性已与真实模型问题彻底分离。是否需要修改 Prompt：
仅 SCN-006 的 any_of 建组属于模型规则执行问题（“和”误为“或”），
其余为场景/协议问题；若需修正，建议新增 GROUP 规则正反例（待独立
Prompt 版本升级任务）。

## 尚未执行的真实验证

- P0-3A 原始运行未通过，验证器修复已完成，重跑待授权；
- 真实 JD 的 v0.8 + Schema V3 抽取未执行（P0-3B 未执行）；
- P0-4 归并验收未执行（无 v0.8 抽取结果）；
- `generate-report` 未实现（P0-5 剩余项）。

## 当前已知问题

- 模型对“和”关系误建 any_of 组（SCN-006，真实 GROUP 错误，建议
  Prompt 规则澄清）；
- SCN-006 场景期望（proficiency 不变）与 Schema V3 规则（项目经验→
  unknown）矛盾，需要修正场景期望；
- SCN-007/SCN-008 占位词 category 判定不稳定，建议场景词替换为
  类别明确的中性描述（不删除 category invariance 检查）。

## 下一步开发任务

1. 根据离线重算结论决定是否提交独立 Prompt 版本升级任务（GROUP
   规则正反例）与场景期望修正；
2. 重新执行 P0-3A 规则场景真实验收；
3. 通过后对单份真实 JD（ID 1）执行 P0-3B；
4. 单份验证通过后扩大到三份 JD（ID 1/2/3）；
5. 三份验证通过后持久化 v0.8 + Schema V3 抽取结果；
6. 执行 P0-4 预检、正式验收和正式归并；
7. 验收通过后实现 `generate-report`。

## 付费与私有数据依赖

- 付费：抽取（v0.8 两段式）与归并（单次 LLM 聚类）调用 LLM，必须
  显式 `--execute`；`validate-consolidation`、合同检查、变形测试不付费。
- 私有：真实 JD（`data/raw_jds/`）、数据库、原始模型响应
  （`data/private/`）与验收原始结果不提交 Git。
