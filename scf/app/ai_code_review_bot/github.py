"""GitHub API客户端 - 使用PAT认证"""

import httpx
import logging
from typing import Any, Optional

from ai_code_review_bot.config import BotConfig

logger = logging.getLogger(__name__)


class GitHubClient:
    """GitHub API客户端，使用PAT认证"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._http = httpx.Client(timeout=30)

    def _get_token(self) -> str:
        """获取GitHub Token（PAT方式）"""
        token = self.config.github_token
        if not token:
            raise ValueError("GitHub Token未配置，请设置CODE_REVIEW_GITHUB_TOKEN环境变量")
        return token

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """获取PR的diff"""
        token = self._get_token()
        resp = self._http.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.diff",
            },
        )
        resp.raise_for_status()
        return resp.text

    def create_review_comment(
        self, owner: str, repo: str, pr_number: int,
        body: str, path: str, line: int, side: str = "RIGHT",
    ) -> dict[str, Any]:
        """创建PR审查评论"""
        token = self._get_token()
        resp = self._http.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "body": body,
                "path": path,
                "line": line,
                "side": side,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def create_review(
        self, owner: str, repo: str, pr_number: int,
        body: str, event: str = "COMMENT",
        comments: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """创建PR审查"""
        token = self._get_token()
        payload: dict[str, Any] = {"body": body, "event": event}
        if comments:
            payload["comments"] = comments
        resp = self._http.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._http.close()
