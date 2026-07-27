"""该模块集中读取LLM环境变量，并在真实抽取前检查必要配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """保存OpenAI兼容LLM接口所需的密钥、地址和模型名称。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LLM_", extra="ignore", case_sensitive=False
    )

    api_key: str = Field(default="")
    base_url: str | None = None
    model: str = Field(default="")

    def missing_fields(self) -> list[str]:
        """返回尚未配置的必要环境变量名称，供CLI生成明确提示。"""
        missing = []
        if not self.api_key:
            missing.append("LLM_API_KEY")
        if not self.model:
            missing.append("LLM_MODEL")
        return missing


def load_llm_settings() -> LLMSettings:
    """从当前环境和项目.env文件加载LLM设置。"""
    return LLMSettings()
