"""AI Code Review Bot - GitHub App版（SCF兼容）

核心改动：
1. PAT认证 → GitHub App JWT + Installation Token
2. 加使用量统计（免费5次/月，Pro无限）
3. 支持installation事件（安装/卸载）
4. 支持Marketplace购买事件
"""

import sys
import os

# SCF Layer路径兼容
for _p in ["/opt/python", "/opt/python/lib/python3.9/site-packages"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "." not in sys.path:
    sys.path.insert(0, ".")

# === config.py ===
import time
from typing import Optional


class BotConfig:
    """Bot配置，从环境变量读取"""

    def __init__(self):
        # GitHub App配置
        self.app_id: str = os.environ.get("GITHUB_APP_ID", "")
        self.app_private_key: str = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
        self.github_webhook_secret: Optional[str] = os.environ.get("GITHUB_WEBHOOK_SECRET")

        # AI配置
        self.ai_api_endpoint: str = os.environ.get("CODE_REVIEW_AI_API_ENDPOINT", "https://api.deepseek.com")
        self.ai_api_key: str = os.environ.get("CODE_REVIEW_AI_API_KEY", "")
        self.ai_model: str = os.environ.get("CODE_REVIEW_AI_MODEL", "deepseek-chat")

        # 审查配置
        self.max_diff_size: int = int(os.environ.get("CODE_REVIEW_MAX_DIFF_SIZE", "10000"))
        self.review_language: str = os.environ.get("CODE_REVIEW_REVIEW_LANGUAGE", "zh-CN")
        self.auto_approve: bool = os.environ.get("CODE_REVIEW_AUTO_APPROVE", "false").lower() == "true"
        self.skip_draft: bool = os.environ.get("CODE_REVIEW_SKIP_DRAFT", "true").lower() == "true"

        # 使用量限制
        self.free_monthly_limit: int = int(os.environ.get("FREE_MONTHLY_LIMIT", "5"))


# === github_app.py ===
"""GitHub App认证 - JWT + Installation Token（纯Python实现，不依赖cryptography）"""

import base64
import hashlib
import httpx
import logging
import json
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GitHubAppAuth:
    """GitHub App认证管理器"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._http = httpx.Client(timeout=30)
        self._installation_tokens: dict[int, tuple[str, float]] = {}  # installation_id -> (token, expires_at)

    def _generate_jwt(self) -> str:
        """生成GitHub App JWT（使用rsa库，不依赖cryptography）"""
        import rsa as rsa_lib

        now = int(time.time())
        payload = {
            "iat": now - 60,       # issued at (60s in past for clock drift)
            "exp": now + 600,      # expiration (10 min max)
            "iss": self.config.app_id,
        }
        # 处理PEM格式的私钥
        key_str = self.config.app_private_key
        if not key_str:
            raise ValueError("GITHUB_APP_PRIVATE_KEY not configured")

        # 解析PEM私钥
        privkey = rsa_lib.PrivateKey.load_pkcs1(key_str.encode())

        # 手动构建JWT: base64url(header).base64url(payload).signature
        header = {"alg": "RS256", "typ": "JWT"}
        header_b64 = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
        signing_input = f"{header_b64}.{payload_b64}"

        # RS256签名: SHA256 + PKCS1v15
        signature = rsa_lib.sign(signing_input.encode(), privkey, "SHA-256")
        sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

        return f"{signing_input}.{sig_b64}"

    def _get_app_headers(self) -> dict[str, str]:
        """获取App级别的请求头"""
        return {
            "Authorization": f"Bearer {self._generate_jwt()}",
            "Accept": "application/vnd.github+json",
        }

    def get_installation_token(self, installation_id: int) -> str:
        """获取installation access token（带缓存）"""
        # 检查缓存
        if installation_id in self._installation_tokens:
            token, expires_at = self._installation_tokens[installation_id]
            if time.time() < expires_at - 60:  # 提前1分钟刷新
                return token

        # 请求新token
        resp = self._http.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers=self._get_app_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["token"]
        # expires_at格式: "2024-01-01T00:00:00Z"
        from datetime import datetime, timezone
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        self._installation_tokens[installation_id] = (token, expires_at)
        logger.info(f"获取installation token: {installation_id}, 过期时间: {data['expires_at']}")
        return token

    def get_installation_repos(self, installation_id: int) -> list[dict]:
        """获取installation可访问的仓库列表"""
        token = self.get_installation_token(installation_id)
        repos = []
        page = 1
        while True:
            resp = self._http.get(
                f"https://api.github.com/installation/repositories",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            repos.extend(data.get("repositories", []))
            if len(data.get("repositories", [])) < 100:
                break
            page += 1
        return repos

    def get_app_info(self) -> Optional[dict]:
        """获取App信息"""
        try:
            resp = self._http.get(
                "https://api.github.com/app",
                headers=self._get_app_headers(),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"获取App信息失败: {e}")
            return None

    def close(self):
        self._http.close()


class GitHubAppClient:
    """GitHub API客户端 - 使用Installation Token"""

    def __init__(self, auth: GitHubAppAuth, installation_id: int):
        self.auth = auth
        self.installation_id = installation_id
        self._http = httpx.Client(timeout=30)

    def _get_headers(self) -> dict[str, str]:
        token = self.auth.get_installation_token(self.installation_id)
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """获取PR的diff"""
        resp = self._http.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={
                **self._get_headers(),
                "Accept": "application/vnd.github.diff",
            },
        )
        resp.raise_for_status()
        return resp.text

    def create_review(
        self, owner: str, repo: str, pr_number: int,
        body: str, event: str = "COMMENT",
        comments: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """创建PR审查"""
        payload: dict[str, Any] = {"body": body, "event": event}
        if comments:
            payload["comments"] = comments
        resp = self._http.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers=self._get_headers(),
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error(f"Create review failed: {resp.status_code} {resp.text[:500]}")
            # Fallback: retry without inline comments
            if comments:
                logger.info("Retrying review without inline comments...")
                payload.pop("comments", None)
                resp = self._http.post(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                    headers=self._get_headers(),
                    json=payload,
                )
                if resp.status_code >= 400:
                    resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    def create_issue_comment(
        self, owner: str, repo: str, pr_number: int, body: str,
    ) -> dict[str, Any]:
        """创建PR评论（非review comment）"""
        resp = self._http.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._get_headers(),
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self._http.close()


# === usage_tracker.py ===
"""使用量追踪 - 基于JSON文件存储（SCF兼容）"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class UsageTracker:
    """使用量追踪器 - 内存存储（SCF无持久化，用环境变量或外部存储）"""

    # 内存缓存（SCF热启动时保留）
    _usage_data: dict[str, dict] = {}

    def __init__(self, config: BotConfig):
        self.config = config

    def _get_month_key(self) -> str:
        """获取当前月份key"""
        from datetime import datetime
        return datetime.utcnow().strftime("%Y-%m")

    def _get_installation_key(self, installation_id: int) -> str:
        """获取installation的存储key"""
        return f"{installation_id}:{self._get_month_key()}"

    def check_and_increment(self, installation_id: int, plan: str = "free") -> tuple[bool, int, int]:
        """检查使用量并递增

        Returns:
            (allowed, current_count, limit)
        """
        if plan == "pro":
            return True, 0, -1  # Pro无限制

        key = self._get_installation_key(installation_id)
        data = self._usage_data.get(key, {"count": 0})
        current = data["count"]
        limit = self.config.free_monthly_limit

        if current >= limit:
            return False, current, limit

        # 递增
        data["count"] = current + 1
        self._usage_data[key] = data
        return True, current + 1, limit

    def get_usage(self, installation_id: int) -> dict:
        """获取当前使用量"""
        key = self._get_installation_key(installation_id)
        data = self._usage_data.get(key, {"count": 0})
        return {
            "count": data["count"],
            "limit": self.config.free_monthly_limit,
            "remaining": max(0, self.config.free_monthly_limit - data["count"]),
            "month": self._get_month_key(),
        }


# === reviewer.py ===
"""代码审查引擎"""

import re
from dataclasses import dataclass, field


@dataclass
class ReviewComment:
    """审查评论"""
    path: str
    line: int
    body: str
    severity: str = "info"
    position: int = 0  # diff position for GitHub review comments API


@dataclass
class ReviewResult:
    """审查结果"""
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)
    approved: bool = True
    score: int = 100


class AIReviewer:
    """AI增强审查"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._http = httpx.Client(timeout=60)

    def review_diff(self, diff: str, rule_result: ReviewResult) -> Optional[str]:
        """用AI审查diff"""
        if not self.config.ai_api_key:
            return None

        max_chars = 8000
        if len(diff) > max_chars:
            diff = diff[:max_chars] + "\n... (diff已截断)"

        rule_summary = f"规则引擎评分: {rule_result.score}/100, 发现 {len(rule_result.comments)} 个问题"
        rule_issues = "\n".join(
            f"- {c.path}:{c.line} [{c.severity}] {c.body}"
            for c in rule_result.comments[:10]
        )

        prompt = f"""你是一个专业的代码审查专家。请审查以下GitHub PR的diff，给出深度分析。

规则引擎已检测出以下问题：
{rule_summary}
{rule_issues if rule_issues else '无'}

请从以下角度分析：
1. **逻辑错误**：是否有逻辑bug、边界条件遗漏、竞态条件等
2. **性能问题**：是否有性能瓶颈、不必要的计算、内存泄漏等
3. **安全风险**：规则引擎可能遗漏的安全问题
4. **代码设计**：命名、结构、可维护性、SOLID原则等
5. **测试覆盖**：是否需要补充测试

请用中文回复，格式如下：
## 🤖 AI审查意见

[总体评价，2-3句话]

### 发现的问题
[如果有新发现的问题，逐条列出]
[如果规则引擎已覆盖所有重要问题，说明"规则引擎已检测出主要问题"即可]

### 改进建议
[1-3条改进建议]

---
以下是需要审查的diff：
```diff
{diff}
```"""

        try:
            api_endpoint = self.config.ai_api_endpoint.rstrip("/")
            resp = self._http.post(
                f"{api_endpoint}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.ai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.ai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"AI审查失败: {e}")
            return f"⚠️ AI审查调用失败: {str(e)[:100]}"


class CodeReviewEngine:
    """代码审查引擎 - 基于规则的静态分析 + AI审查"""

    DANGEROUS_FUNCTIONS = {
        "python": [
            ("eval(", "使用eval()存在代码注入风险，建议使用ast.literal_eval()"),
            ("exec(", "使用exec()存在代码注入风险"),
            ("__import__(", "动态导入可能存在安全风险"),
            ("subprocess.call(", "建议使用subprocess.run()并设置shell=False"),
            ("os.system(", "os.system()存在命令注入风险，建议使用subprocess"),
            ("pickle.loads(", "pickle反序列化存在安全风险"),
            ("yaml.load(", "建议使用yaml.safe_load()代替yaml.load()"),
            ("except:", "空except会捕获所有异常包括KeyboardInterrupt，建议指定异常类型"),
        ],
        "javascript": [
            ("eval(", "使用eval()存在代码注入风险"),
            ("innerHTML", "直接设置innerHTML存在XSS风险，建议使用textContent"),
            ("document.write(", "document.write()存在XSS风险"),
            ("new Function(", "动态创建函数存在代码注入风险"),
        ],
        "go": [
            ("os/exec.Command(", "命令执行需确保参数不被用户控制"),
        ],
    }

    QUALITY_RULES = [
        (r"TODO|FIXME|HACK|XXX", "发现待办标记，建议在上线前处理", "info"),
        (r"password\s*=\s*['\"]", "疑似硬编码密码，建议使用环境变量", "error"),
        (r"api_key\s*=\s*['\"]", "疑似硬编码API密钥，建议使用环境变量", "error"),
        (r"secret\s*=\s*['\"]", "疑似硬编码密钥，建议使用环境变量", "error"),
        (r"console\.log\(", "生产代码中不应保留console.log", "warning"),
        (r"print\(", "生产代码中不应保留print调试语句", "warning"),
        (r"debugger", "生产代码中不应保留debugger断点", "error"),
        (r"catch\s*\(\w*\)\s*\{\s*\}", "空catch块会吞掉异常，建议至少记录日志", "warning"),
        (r"except\s*:\s*pass", "空except会吞掉异常，建议至少记录日志", "warning"),
    ]

    def review_diff(self, diff: str, language: Optional[str] = None) -> ReviewResult:
        """审查PR diff"""
        comments: list[ReviewComment] = []
        score = 100

        files = self._parse_diff(diff)

        for file_path, file_diff in files.items():
            detected_lang = language or self._detect_language(file_path)

            lang_dangers = self.DANGEROUS_FUNCTIONS.get(detected_lang, [])
            for line_num, line_content in file_diff.added_lines.items():
                for pattern, message in lang_dangers:
                    if pattern in line_content:
                        comments.append(ReviewComment(
                            path=file_path, line=line_num,
                            body=f"⚠️ **安全警告**: {message}", severity="error",
                            position=file_diff.line_positions.get(line_num, 0),
                        ))
                        score -= 15

            for line_num, line_content in file_diff.added_lines.items():
                for pattern, message, severity in self.QUALITY_RULES:
                    if re.search(pattern, line_content, re.IGNORECASE):
                        comments.append(ReviewComment(
                            path=file_path, line=line_num,
                            body=f"{'🔴' if severity == 'error' else '🟡' if severity == 'warning' else 'ℹ️'} {message}",
                            severity=severity,
                            position=file_diff.line_positions.get(line_num, 0),
                        ))
                        if severity == "error":
                            score -= 10
                        elif severity == "warning":
                            score -= 5

        score = max(0, score)
        approved = score >= 60 and not any(c.severity == "error" for c in comments)

        summary_parts = [f"代码质量评分: **{score}/100**"]
        if comments:
            error_count = sum(1 for c in comments if c.severity == "error")
            warning_count = sum(1 for c in comments if c.severity == "warning")
            info_count = sum(1 for c in comments if c.severity == "info")
            summary_parts.append(f"发现 {error_count} 个严重问题, {warning_count} 个警告, {info_count} 个建议")
        else:
            summary_parts.append("未发现明显问题 ✅")

        if approved:
            summary_parts.append("建议: ✅ 可以合并")
        else:
            summary_parts.append("建议: ❌ 需要修改后再合并")

        return ReviewResult(
            summary="\n\n".join(summary_parts),
            comments=comments, approved=approved, score=score,
        )

    def _parse_diff(self, diff: str) -> dict[str, "FileDiff"]:
        """解析diff，计算每个文件的行号和position

        GitHub review comments API的position是每个文件diff中的相对行号（1-based），
        从diff --git行开始计数。
        """
        files: dict[str, FileDiff] = {}
        current_file = None
        current_line = 0
        file_position = 0  # position within current file's diff (1-based)

        for line in diff.split("\n"):
            if line.startswith("diff --git"):
                match = re.search(r"b/(.+)", line)
                if match:
                    current_file = match.group(1)
                    files[current_file] = FileDiff(path=current_file)
                    file_position = 0
            elif current_file:
                file_position += 1
                if line.startswith("@@"):
                    match = re.search(r"\+(\d+)", line)
                    if match:
                        current_line = int(match.group(1)) - 1
                elif line.startswith("+") and not line.startswith("+++"):
                    current_line += 1
                    files[current_file].added_lines[current_line] = line[1:]
                    files[current_file].line_positions[current_line] = file_position
                elif not line.startswith("-") and not line.startswith("\\"):
                    current_line += 1

        return files

    def _detect_language(self, file_path: str) -> str:
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "javascript",
            ".jsx": "javascript", ".tsx": "javascript", ".go": "go",
            ".rs": "rust", ".java": "java", ".rb": "ruby",
        }
        for ext, lang in ext_map.items():
            if file_path.endswith(ext):
                return lang
        return "unknown"


@dataclass
class FileDiff:
    path: str
    added_lines: dict[int, str] = field(default_factory=dict)
    # line_number -> position in diff (1-based index for GitHub review comments API)
    line_positions: dict[int, int] = field(default_factory=dict)


# === webhook_handler.py ===
"""GitHub App Webhook处理器"""

import hmac
import hashlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WebhookHandler:
    """GitHub App Webhook处理器"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.auth = GitHubAppAuth(config)
        self.reviewer = CodeReviewEngine()
        self.ai_reviewer = AIReviewer(config)
        self.usage_tracker = UsageTracker(config)

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """验证Webhook签名"""
        if not self.config.github_webhook_secret:
            return True
        expected = "sha256=" + hmac.new(
            self.config.github_webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def handle_installation(self, event: dict[str, Any]) -> dict:
        """处理installation事件"""
        action = event.get("action")
        installation = event.get("installation", {})
        installation_id = installation.get("id")

        logger.info(f"Installation event: {action}, id={installation_id}")

        if action == "created":
            # 新安装 - 可以发欢迎消息
            logger.info(f"App installed: installation_id={installation_id}")
            return {"status": "installed", "installation_id": installation_id}

        elif action == "deleted":
            logger.info(f"App uninstalled: installation_id={installation_id}")
            return {"status": "uninstalled", "installation_id": installation_id}

        return {"status": "ignored", "action": action}

    def handle_installation_repos(self, event: dict[str, Any]) -> dict:
        """处理installation_repositories事件"""
        action = event.get("action")
        logger.info(f"Installation repos event: {action}")
        return {"status": "ok", "action": action}

    def handle_marketplace_purchase(self, event: dict[str, Any]) -> dict:
        """处理Marketplace购买事件"""
        action = event.get("action")
        purchase = event.get("marketplace_purchase", {})
        account = purchase.get("account", {})
        plan = purchase.get("plan", {})

        logger.info(
            f"Marketplace event: {action}, "
            f"account={account.get('login')}, "
            f"plan={plan.get('name')}"
        )

        return {
            "status": "ok",
            "action": action,
            "account": account.get("login"),
            "plan": plan.get("name"),
        }

    async def handle_pull_request(self, event: dict[str, Any]) -> Optional[dict]:
        """处理pull_request事件"""
        action = event.get("action")
        pr = event.get("pull_request", {})

        if action not in ("opened", "synchronize"):
            return {"status": "ignored", "action": action}

        if self.config.skip_draft and pr.get("draft", False):
            return {"status": "skipped", "reason": "draft_pr"}

        changed_files = pr.get("changed_files", 0)
        if changed_files > 50:
            return {"status": "skipped", "reason": "too_many_files", "count": changed_files}

        # 获取installation_id
        installation = event.get("installation", {})
        installation_id = installation.get("id")
        if not installation_id:
            return {"status": "error", "error": "missing_installation_id"}

        repo = event.get("repository", {})
        owner = repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")
        pr_number = pr.get("number", 0)

        if not owner or not repo_name:
            return {"status": "error", "error": "missing_repo_info"}

        # 检查使用量
        # TODO: 从Marketplace API获取用户plan，暂时默认free
        plan = "free"
        allowed, current, limit = self.usage_tracker.check_and_increment(installation_id, plan)

        if not allowed:
            # 超出免费额度，发提示评论
            try:
                client = GitHubAppClient(self.auth, installation_id)
                client.create_issue_comment(
                    owner, repo_name, pr_number,
                    f"⚠️ **免费额度已用完**\n\n"
                    f"本月已使用 {current} 次审查，免费额度为 {limit} 次/月。\n\n"
                    f"升级到 Pro 版可获得无限审查次数：[升级链接](https://github.com/marketplace/ai-code-review-bot)\n\n"
                    f"<details><summary>💡 如何升级</summary>\n\n"
                    f"1. 前往 [GitHub Marketplace](https://github.com/marketplace/ai-code-review-bot)\n"
                    f"2. 选择 Pro 计划\n"
                    f"3. 完成付款后自动生效\n</details>"
                )
                client.close()
            except Exception as e:
                logger.error(f"发送额度提示失败: {e}")

            return {
                "status": "limit_exceeded",
                "installation_id": installation_id,
                "current": current,
                "limit": limit,
            }

        try:
            client = GitHubAppClient(self.auth, installation_id)

            # 获取diff
            diff = client.get_pr_diff(owner, repo_name, pr_number)

            # 规则引擎审查
            result = self.reviewer.review_diff(diff)

            # AI增强审查
            ai_review = self.ai_reviewer.review_diff(diff, result)

            # 构建审查结果
            event_type = "COMMENT"

            summary_lines = [result.summary]

            # 使用量提示（免费用户）
            if plan == "free":
                usage = self.usage_tracker.get_usage(installation_id)
                summary_lines.append(
                    f"\n---\n📊 **本月使用量**: {usage['count']}/{usage['limit']} "
                    f"(剩余 {usage['remaining']} 次) | "
                    f"[升级Pro无限审查](https://github.com/marketplace/ai-code-review-bot)"
                )

            if result.comments:
                summary_lines.append("\n---\n**📋 规则引擎审查详情：**\n")
                for i, c in enumerate(result.comments[:20], 1):
                    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(c.severity, "ℹ️")
                    summary_lines.append(f"{severity_emoji} **{c.path}:{c.line}** — {c.body}")

            if ai_review:
                summary_lines.append(f"\n---\n{ai_review}")

            full_summary = "\n".join(summary_lines)

            # 构建行级评论（使用line+side，比position更可靠）
            review_comments = []
            for c in result.comments[:20]:
                review_comments.append({
                    "path": c.path,
                    "line": c.line,
                    "side": "RIGHT",
                    "body": c.body,
                })

            client.create_review(
                owner, repo_name, pr_number,
                body=full_summary,
                event=event_type,
                comments=review_comments if review_comments else None,
            )
            client.close()

            return {
                "status": "reviewed",
                "pr": f"{owner}/{repo_name}#{pr_number}",
                "score": result.score,
                "approved": result.approved,
                "comment_count": len(result.comments),
                "plan": plan,
                "usage": f"{current}/{limit}" if plan == "free" else "unlimited",
            }

        except Exception as e:
            logger.error(f"审查PR失败: {e}")
            return {"status": "error", "error": str(e)}

    def close(self):
        self.auth.close()


# === index.py (main_handler) ===
"""腾讯云SCF入口 - AI Code Review Bot (GitHub App版)"""

import json
import logging
import traceback

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_handler = None


def _get_handler() -> WebhookHandler:
    global _handler
    if _handler is None:
        config = BotConfig()
        _handler = WebhookHandler(config)
    return _handler


def main_handler(event: dict, context: dict) -> dict:
    """SCF入口函数"""
    try:
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
                    "version": "2.0.0",
                    "mode": "github-app",
                }),
            }

        if http_method != "POST":
            return {
                "statusCode": 405,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Method not allowed"}),
            }

        # 验证签名（SCF headers可能是小写）
        handler = _get_handler()
        signature = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256", "")
        github_event = headers.get("X-GitHub-Event") or headers.get("x-github-event", "")
        payload = body.encode("utf-8") if isinstance(body, str) else body

        if not handler.verify_signature(payload, signature):
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid signature"}),
            }

        # 解析事件（github_event已在上方提取）
        payload_data = json.loads(body) if isinstance(body, str) else body

        logger.info(f"收到GitHub事件: {github_event}")

        # 路由到不同处理器
        if github_event == "installation":
            result = handler.handle_installation(payload_data)
        elif github_event == "installation_repositories":
            result = handler.handle_installation_repos(payload_data)
        elif github_event == "marketplace_purchase":
            result = handler.handle_marketplace_purchase(payload_data)
        elif github_event == "pull_request":
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                handler.handle_pull_request(payload_data)
            )
        else:
            result = {"status": "ignored", "event": github_event}

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
