"""Webhook处理服务"""

import hmac
import hashlib
import json
import logging
from typing import Any, Optional

from ai_code_review_bot.config import BotConfig
from ai_code_review_bot.github import GitHubClient
from ai_code_review_bot.reviewer import CodeReviewEngine

logger = logging.getLogger(__name__)


class WebhookHandler:
    """GitHub Webhook处理器"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.github = GitHubClient(config)
        self.reviewer = CodeReviewEngine()

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """验证Webhook签名"""
        if not self.config.github_webhook_secret:
            return True  # 未配置密钥时跳过验证
        expected = "sha256=" + hmac.new(
            self.config.github_webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def handle_pull_request(self, event: dict[str, Any]) -> Optional[dict]:
        """处理pull_request事件"""
        action = event.get("action")
        pr = event.get("pull_request", {})

        # 只处理opened和synchronize事件
        if action not in ("opened", "synchronize"):
            return {"status": "ignored", "action": action}

        # 跳过Draft PR
        if self.config.skip_draft and pr.get("draft", False):
            return {"status": "skipped", "reason": "draft_pr"}

        # 跳过大型PR
        changed_files = pr.get("changed_files", 0)
        if changed_files > 50:
            return {"status": "skipped", "reason": "too_many_files", "count": changed_files}

        repo = event.get("repository", {})
        owner = repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")
        pr_number = pr.get("number", 0)

        if not owner or not repo_name:
            return {"status": "error", "error": "missing_repo_info"}

        try:
            # 获取diff
            diff = self.github.get_pr_diff(owner, repo_name, pr_number)

            # 审查代码
            result = self.reviewer.review_diff(diff)

            # 提交审查
            review_comments = [
                {
                    "path": c.path,
                    "line": c.line,
                    "body": c.body,
                }
                for c in result.comments[:20]  # 最多20条评论
            ]

            event_type = "APPROVE" if result.approved else "REQUEST_CHANGES"

            self.github.create_review(
                owner, repo_name, pr_number,
                body=result.summary,
                event=event_type,
                comments=review_comments if review_comments else None,
            )

            return {
                "status": "reviewed",
                "pr": f"{owner}/{repo_name}#{pr_number}",
                "score": result.score,
                "approved": result.approved,
                "comment_count": len(result.comments),
            }

        except Exception as e:
            logger.error(f"审查PR失败: {e}")
            return {"status": "error", "error": str(e)}

    def close(self):
        self.github.close()
