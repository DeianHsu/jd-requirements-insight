# 岗位职责边界（RESP）

> 只在标注职责边界或检查"职责不得误抽成候选人要求"时读取本文。
> 规则 ID：`RESP-01`～`RESP-02`。修改规则语义必须同步更新本文件、
> Prompt 引用和测试。

## RESP-01 职责不是岗位要求

responsibility 块中的工作内容是候选人入职后需要完成的工作，不是岗位
要求：不得从 responsibility 块抽取 requirement。职责中出现的技术只在
JD 明确要求候选人掌握时才成为要求（否则至多 mentioned）。

## RESP-02 mixed 块分离

mixed 块先分离工作部分与条件部分，只把候选人条件部分抽取为 requirement，
工作内容不进入 requirement。

## 验证边界

- 发现段仍区分 `responsibility` / `requirement` / `mixed` / `excluded`
  候选块（COVER-01～COVER-03）；
- 合同检查：responsibility 块产出 requirement 即类型违规（COVER-04）；
- 职责本身不再做结构化持久化、稳定性评测或专项测试——当前主线只消费
  岗位要求；职责信息保留在 JD 原文与发现段候选块中。
