# 岗位要求市场分析报告（流程演示）
> **样本限制**：本报告基于当前已定稿归并批次（3 份 JD、9 条 requirement instances、6 个 canonical requirements）生成，是**流程与证据追溯能力演示**，不代表完整岗位市场结论。所有频率与排名仅在当前样本范围内有效，不得称为行业排名。
## 报告身份
- 归并批次：#1（job_ids=1,2,3）
- JD 数量：3
- requirement instance 数：9
- canonical requirement 数：6
- 抽取器版本：test-model|prompt:0.10|schema:3.0
- 归并器版本：test-model|prompt:4.3|schema:3.0
- 输入身份：2abe835c4790…
- 来源 JD：1、2、3
### 来源 JD 摘要
- JD 1：示例科技｜大模型应用工程师｜北京
- JD 2：示例智能｜Agent 开发工程师｜上海
- JD 3：示例数据｜RAG 平台工程师｜深圳

## 总览
- 覆盖 JD 数：3
- 抽取原子要求数：9
- 归并标准要求数：6
- 出现在多份 JD 的要求数：2
- 仅出现在单份 JD 的要求数（长尾）：4
- 覆盖 JD 最多的要求：**编程语言**（3/3 份 JD）

## 跨 JD 共同要求
| 要求 | JD 覆盖数 | JD 覆盖率 | 实例数 | JD 级 importance |
| --- | --- | --- | --- | --- |
| 编程语言 | 3 | 100% | 3 | must 2 / preferred 1 |
| 大模型应用开发经验 | 2 | 67% | 2 | must 1 / preferred 1 |

## 单 JD 特有要求（长尾）
| 要求 | JD 覆盖数 | JD 覆盖率 | 实例数 | JD 级 importance |
| --- | --- | --- | --- | --- |
| RAG 应用开发 | 1 | 33% | 1 | must 1 |
| 团队协作能力 | 1 | 33% | 1 | must 1 |
| 数据分析经验 | 1 | 33% | 1 | preferred 1 |
| 本科及以上学历 | 1 | 33% | 1 | must 1 |

## 证据追溯
### 编程语言
来源：3 份 JD（JD 1、JD 2、JD 3），3 个实例；JD 级 importance：must 2 / preferred 1
- JD 1｜实例 1：**编程语言**
  - importance=must / category=programming\_language / proficiency=basic
  - 证据：
    > 1. 熟悉主流编程语言。

- JD 2｜实例 4：**编程语言**
  - importance=must / category=programming\_language / proficiency=advanced
  - 证据：
    > 1. 掌握常用编程语言。

- JD 3｜实例 7：**编程语言**
  - importance=preferred / category=programming\_language / proficiency=basic
  - 证据：
    > 1. 熟悉编程语言者加分。
### 大模型应用开发经验
来源：2 份 JD（JD 1、JD 2），2 个实例；JD 级 importance：must 1 / preferred 1
- JD 1｜实例 2：**大模型应用开发经验**
  - importance=must / category=experience / proficiency=unknown
  - 证据：
    > 2. 有 LLM 应用落地经验。

- JD 2｜实例 5：**大模型应用开发经验**
  - importance=preferred / category=experience / proficiency=unknown
  - 证据：
    > 2. 具备大模型应用开发经验者加分。
### RAG 应用开发
来源：1 份 JD（JD 3），1 个实例；JD 级 importance：must 1
- JD 3｜实例 8：**RAG 应用开发**
  - importance=must / category=rag / proficiency=basic
  - 证据：
    > 2. 熟悉 RAG 应用开发。
### 团队协作能力
来源：1 份 JD（JD 2），1 个实例；JD 级 importance：must 1
- JD 2｜实例 6：**团队协作能力**
  - importance=must / category=soft\_skill / proficiency=unknown
  - 证据：
    > 3. 具备跨团队协作能力。
### 数据分析经验
来源：1 份 JD（JD 1），1 个实例；JD 级 importance：preferred 1
- JD 1｜实例 3：**数据分析经验**
  - importance=preferred / category=experience / proficiency=unknown
  - 证据：
    > 3. 有数据分析经验者优先。
### 本科及以上学历
来源：1 份 JD（JD 3），1 个实例；JD 级 importance：must 1
- JD 3｜实例 9：**本科及以上学历**
  - importance=must / category=education / proficiency=unknown
  - 证据：
    > 3. 本科及以上学历。
## 方法与限制
- 市场频率以**独立 JD 数**为主口径（同一 JD 中同一 canonical 的多个实例只贡献一次 JD 覆盖），实例数作为抽取粒度补充指标。
- JD 级 importance 按 `must > preferred > mentioned > unknown` 归并；实例级 importance 仅作诊断参考。
- 排序：独立 JD 数降序 → 实例数降序 → 名称升序。
- 每个 canonical 均可在「证据追溯」中回查来源 JD、原始要求与原文 evidence。
- 本报告为归并批次的**可再生派生产物**：重新生成会覆盖旧文件，内容由同一批次确定性决定。
- **样本限制**：当前样本为 3 份 JD，统计结论不得外推为市场结论。
