"""该模块提供FastAPI应用实例和最小环境验证入口。"""

from fastapi import FastAPI

app = FastAPI(
    title="JD Skill Insight",
    description="面向个人求职决策的岗位技能洞察系统",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """返回轻量健康状态，用于确认Web应用能够正常响应。"""
    return {"status": "ok"}


def main() -> None:
    """打印环境就绪信息，用于快速验证Python模块可以正常运行。"""
    print("JD Skill Insight environment is ready.")


if __name__ == "__main__":
    main()
