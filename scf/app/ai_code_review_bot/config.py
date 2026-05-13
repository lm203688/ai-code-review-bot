"""配置 - 纯Python实现，不依赖pydantic"""

import os
from typing import Optional


class BotConfig:
    """Bot配置，从环境变量读取"""

    def __init__(self):
        # GitHub配置
        self.github_token: str = os.environ.get("CODE_REVIEW_GITHUB_TOKEN", "")
        self.github_webhook_secret: Optional[str] = os.environ.get("CODE_REVIEW_GITHUB_WEBHOOK_SECRET")

        # AI配置
        self.ai_api_endpoint: str = os.environ.get("CODE_REVIEW_AI_API_ENDPOINT", "https://api.zhipuai.cn/v4")
        self.ai_api_key: str = os.environ.get("CODE_REVIEW_AI_API_KEY", "")
        self.ai_model: str = os.environ.get("CODE_REVIEW_AI_MODEL", "glm-4")

        # 审查配置
        self.max_diff_size: int = int(os.environ.get("CODE_REVIEW_MAX_DIFF_SIZE", "10000"))
        self.review_language: str = os.environ.get("CODE_REVIEW_REVIEW_LANGUAGE", "zh-CN")
        self.auto_approve: bool = os.environ.get("CODE_REVIEW_AUTO_APPROVE", "false").lower() == "true"
        self.skip_draft: bool = os.environ.get("CODE_REVIEW_SKIP_DRAFT", "true").lower() == "true"

        # 服务配置
        self.host: str = os.environ.get("CODE_REVIEW_HOST", "0.0.0.0")
        self.port: int = int(os.environ.get("CODE_REVIEW_PORT", "8080"))
