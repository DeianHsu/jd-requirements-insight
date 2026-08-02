"""生成P0-3/P0-5未见验证集标注草稿：按P0-1标注规范为10条系统选取的候选句生成期望项。

草稿未冻结：写入后 split_status.validation 保持 pending_creation，
只有用户人工审核批准后才置为 approved_frozen 并用于正式验收。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

CASES_PATH = Path("data/private/annotation_cases.json")
BACKUP_PATH = Path(
    "data/private/experiments/p0_3/annotation_cases_backup_pre_validation.json"
)


def _req(
    raw_name: str, category: str, importance: str, proficiency: str
) -> dict[str, object]:
    """构造一条要求期望项，证据随后统一填充为整句。"""
    return {
        "raw_name": raw_name,
        "category": category,
        "importance": importance,
        "proficiency": proficiency,
        "group_id": None,
        "group_logic": "standalone",
        "min_years": None,
        "max_years": None,
        "years_text": None,
        "evidence": None,
        "confidence": 1.0,
    }


def _resp(name: str, evidence: str) -> dict[str, str]:
    """构造一条职责期望项。"""
    return {"name": name, "evidence": evidence}


def build_validation_cases() -> list[dict[str, object]]:
    """按标注规范生成10条validation case（case_016~case_025）。"""
    return [
        {
            "case_id": "case_016",
            "dataset_split": "validation",
            "source_file": "jd_001_ai_agent_开发工程师_上海熠速.md",
            "section": "任职描述",
            "annotation_target": "responsibilities",
            "sentence": "参与 Agent 核心能力建设，包括 Workflow、多 Agent 协同、Skill 与模板管理、Memory 记忆机制、版本管理以及业务系统集成。",
            "expected": {
                "responsibilities": [
                    _resp(
                        "参与 Agent 核心能力建设",
                        "参与 Agent 核心能力建设，包括 Workflow、多 Agent 协同、Skill 与模板管理、Memory 记忆机制、版本管理以及业务系统集成。",
                    )
                ],
                "requirements": [],
            },
            "decision_notes": [
                "Workflow、多 Agent 协同等是Agent核心能力的组成部分（能力属性列举），不拆为独立职责（RESPONSIBILITIES第3节）。"
            ],
        },
        {
            "case_id": "case_017",
            "dataset_split": "validation",
            "source_file": "jd_001_ai_agent_开发工程师_上海熠速.md",
            "section": "任职技能要求",
            "annotation_target": "requirements",
            "sentence": "具备本科及以上学历，计算机、软件工程、人工智能、自动化等相关专业优先。",
            "expected": {
                "responsibilities": [],
                "requirements": [
                    _req("本科及以上学历", "education", "must", "unknown"),
                    _req(
                        "计算机、软件工程、人工智能、自动化等相关专业",
                        "domain_knowledge",
                        "preferred",
                        "unknown",
                    ),
                ],
            },
            "decision_notes": [
                "学历must、专业preferred（句末优先）。",
                "用户裁决（2026-08-01）：专业列举保留上位『相关专业』单条（对照DEC-017 case_003先例，『等』为非穷举示例），不拆成多个standalone；raw_name按REQUIREMENTS第9节保留原文表达。",
            ],
        },
        {
            "case_id": "case_018",
            "dataset_split": "validation",
            "source_file": "jd_002_大模型应用开发_ai_agent方向_思格新能源.md",
            "section": "职位描述",
            "annotation_target": "responsibilities",
            "sentence": "设计高扩展性的 Agent 调度与编排框架",
            "expected": {
                "responsibilities": [
                    _resp(
                        "设计 Agent 调度与编排框架",
                        "设计高扩展性的 Agent 调度与编排框架",
                    )
                ],
                "requirements": [],
            },
            "decision_notes": [
                "高扩展性是对象属性修饰，name保留动作+对象（RESPONSIBILITIES第4节）。"
            ],
        },
        {
            "case_id": "case_019",
            "dataset_split": "validation",
            "source_file": "jd_002_大模型应用开发_ai_agent方向_思格新能源.md",
            "section": "职位要求",
            "annotation_target": "requirements",
            "sentence": "熟悉 LLM 应用开发、Prompt Engineering、RAG、Function Calling 等技术原理",
            "expected": {
                "responsibilities": [],
                "requirements": [
                    _req("LLM应用开发", "llm_application", "must", "familiar"),
                    _req("Prompt Engineering", "llm_application", "must", "familiar"),
                    _req("RAG", "rag", "must", "familiar"),
                    _req("Function Calling", "agent_capability", "must", "familiar"),
                ],
            },
            "decision_notes": [
                "具体技术名被熟悉直接修饰且带等，逐项保留（REQUIREMENTS第4节）。",
                "等技术原理是上位修饰语，不单独成项。",
            ],
        },
        {
            "case_id": "case_020",
            "dataset_split": "validation",
            "source_file": "jd_003_ai_agent开发工程师_上海序祯达生物科技.md",
            "section": "工作内容",
            "annotation_target": "responsibilities",
            "sentence": "对接生物信息学团队与业务部门，精准拆解科研需求，将复杂业务流程转化为可执行的 Agent 任务链。",
            "expected": {
                "responsibilities": [
                    _resp(
                        "将复杂业务流程转化为可执行的 Agent 任务链",
                        "对接生物信息学团队与业务部门，精准拆解科研需求，将复杂业务流程转化为可执行的 Agent 任务链。",
                    )
                ],
                "requirements": [],
            },
            "decision_notes": [
                "对接团队与部门是协作对象（实施方式），拆解需求+转化任务链构成单一端到端交付（RESPONSIBILITIES第3节）。",
                "歧义点：拆解科研需求也可能被拆为独立职责，按端到端交付保留1项。",
            ],
        },
        {
            "case_id": "case_021",
            "dataset_split": "validation",
            "source_file": "jd_003_ai_agent开发工程师_上海序祯达生物科技.md",
            "section": "任职要求",
            "annotation_target": "requirements",
            "sentence": "计算机生物学、生物信息学相关专业硕博，具备扎实的 Python 编程基础与算法功底",
            "expected": {
                "responsibilities": [],
                "requirements": [
                    _req(
                        "计算机生物学、生物信息学相关专业",
                        "domain_knowledge",
                        "must",
                        "unknown",
                    ),
                    _req("硕博学历", "education", "must", "unknown"),
                    _req("Python", "programming_language", "must", "proficient"),
                    _req("算法功底", "software_engineering", "must", "proficient"),
                ],
            },
            "decision_notes": [
                "用户裁决（2026-08-01）：专业列举保留上位单条（与case_017口径一致）。",
                "硕博=硕士或博士，原文无或等显式任选词，按整体学历条件标注standalone；专业与学历跨类别拆开（REQUIREMENTS第2节）。",
                "扎实→proficient（REQUIREMENTS第7节）；编程基础与算法功底是两项可独立评价的条件，拆开。",
            ],
        },
        {
            "case_id": "case_022",
            "dataset_split": "validation",
            "source_file": "jd_004_ai研发工程师_江苏正信信息安全测试.md",
            "section": "岗位职责",
            "annotation_target": "responsibilities",
            "sentence": "AI 应用系统开发：设计并开发基于 AI 技术的业务系统，包括但不限于智能问答、知识库检索（RAG）、文档智能处理、智能推荐等应用场景",
            "expected": {
                "responsibilities": [
                    _resp(
                        "设计并开发基于 AI 技术的业务系统",
                        "AI 应用系统开发：设计并开发基于 AI 技术的业务系统，包括但不限于智能问答、知识库检索（RAG）、文档智能处理、智能推荐等应用场景",
                    )
                ],
                "requirements": [],
            },
            "decision_notes": [
                "冒号前AI 应用系统开发是环节标签，随evidence保留。",
                "包括但不限于…等应用场景是非穷举示例，不拆为独立职责（RESPONSIBILITIES第3节）。",
            ],
        },
        {
            "case_id": "case_023",
            "dataset_split": "validation",
            "source_file": "jd_004_ai研发工程师_江苏正信信息安全测试.md",
            "section": "任职要求",
            "annotation_target": "requirements",
            "sentence": "研究生以上学历，计算机科学、人工智能、数学、电子信息等相关专业",
            "expected": {
                "responsibilities": [],
                "requirements": [
                    _req("研究生以上学历", "education", "must", "unknown"),
                    _req(
                        "计算机科学、人工智能、数学、电子信息等相关专业",
                        "domain_knowledge",
                        "must",
                        "unknown",
                    ),
                ],
            },
            "decision_notes": [
                "用户裁决（2026-08-01）：专业列举保留上位单条（与case_017口径一致），raw_name保留原文表达。"
            ],
        },
        {
            "case_id": "case_024",
            "dataset_split": "validation",
            "source_file": "jd_005_ai_agent方向工程师_追觅智净未来.md",
            "section": "岗位职责",
            "annotation_target": "responsibilities",
            "sentence": "参与 AI Agent 设计与实现，包括任务拆解、工具调用、多轮对话等",
            "expected": {
                "responsibilities": [
                    _resp(
                        "参与 AI Agent 设计与实现",
                        "参与 AI Agent 设计与实现，包括任务拆解、工具调用、多轮对话等",
                    )
                ],
                "requirements": [],
            },
            "decision_notes": [
                "任务拆解、工具调用、多轮对话是Agent能力的组成部分（能力属性列举），不拆（RESPONSIBILITIES第3节）。"
            ],
        },
        {
            "case_id": "case_025",
            "dataset_split": "validation",
            "source_file": "jd_005_ai_agent方向工程师_追觅智净未来.md",
            "section": "任职要求",
            "annotation_target": "requirements",
            "sentence": "有实际 AI 应用项目经验（如 Agent、AI 问答、自动化流程等）",
            "expected": {
                "responsibilities": [],
                "requirements": [
                    _req("AI应用项目经验", "experience", "must", "unknown"),
                ],
            },
            "decision_notes": [
                "括号如…等是非穷举示例，只标注上位AI应用项目经验（REQUIREMENTS第4节）。",
                "项目经验不推断熟练度→unknown（REQUIREMENTS第7节）。",
            ],
        },
    ]


def main() -> None:
    """备份标注文件并重建validation草稿case（幂等：先移除旧validation再追加）。"""
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP_PATH.exists():
        shutil.copyfile(CASES_PATH, BACKUP_PATH)

    new_cases = build_validation_cases()
    for case in new_cases:
        for item in case["expected"]["requirements"]:
            item["evidence"] = case["sentence"]
    # 幂等：移除旧的validation草稿后追加，避免重复运行产生重复case。
    cases["cases"] = [
        c for c in cases["cases"] if c.get("dataset_split") != "validation"
    ]
    cases["cases"].extend(new_cases)
    # 草稿未批准：保持 pending_creation，只有人工审核后才置为 approved_frozen。
    cases["split_status"]["validation"] = "pending_creation"
    CASES_PATH.write_text(
        json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"已重建{len(new_cases)}条validation case（case_016~case_025），"
        f"总case数{len(cases['cases'])}，split_status={cases['split_status']}"
    )


if __name__ == "__main__":
    main()
