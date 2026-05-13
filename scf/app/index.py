"""腾讯云SCF入口 - AI Code Review Bot"""

import json
import logging
import traceback

from ai_code_review_bot.config import BotConfig
from ai_code_review_bot.webhook import WebhookHandler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 全局初始化（SCF冷启动时执行）
_handler = None


def _get_handler() -> WebhookHandler:
    global _handler
    if _handler is None:
        config = BotConfig()
        _handler = WebhookHandler(config)
    return _handler


def main_handler(event: dict, context: dict) -> dict:
    """SCF入口函数

    API网关触发器事件格式:
    event = {
        "httpMethod": "POST",
        "headers": {...},
        "body": "...",       # JSON字符串
        "queryString": {},
        "pathParameters": {},
        ...
    }
    """
    try:
        # 解析请求
        http_method = event.get("httpMethod", "")
        headers = event.get("headers", {})
        body = event.get("body", "{}")

        # 健康检查
        if http_method == "GET":
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "status": "ok",
                    "service": "ai-code-review-bot",
                    "version": "0.1.0",
                }),
            }

        # 只处理POST（GitHub Webhook）
        if http_method != "POST":
            return {
                "statusCode": 405,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Method not allowed"}),
            }

        # 验证签名
        handler = _get_handler()
        signature = headers.get("X-Hub-Signature-256", "")
        payload = body.encode("utf-8") if isinstance(body, str) else body

        if not handler.verify_signature(payload, signature):
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid signature"}),
            }

        # 解析事件
        github_event = headers.get("X-GitHub-Event", "")
        payload_data = json.loads(body) if isinstance(body, str) else body

        logger.info(f"收到GitHub事件: {github_event}")

        # 只处理pull_request事件
        if github_event != "pull_request":
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "status": "ignored",
                    "event": github_event,
                }),
            }

        # 处理PR审查
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            handler.handle_pull_request(payload_data)
        )

        status_code = 200 if result and result.get("status") != "error" else 500
        return {
            "statusCode": status_code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result or {"status": "no_action"}),
        }

    except Exception as e:
        logger.error(f"处理请求失败: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
