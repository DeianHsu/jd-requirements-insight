"""该模块验证FastAPI健康检查接口能够正常响应。"""

from fastapi.testclient import TestClient

from app.main import app


def test_application_metadata() -> None:
    """验证应用元数据使用JD Skill Insight的新名称和产品定位。"""
    assert app.title == "JD Skill Insight"
    assert app.description == "面向个人求职决策的岗位技能洞察系统"


def test_health() -> None:
    """验证健康检查接口返回200状态码和预期JSON内容。"""
    client = TestClient(app)

    # 通过真实HTTP测试客户端调用接口，同时覆盖路由注册和响应序列化。
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
