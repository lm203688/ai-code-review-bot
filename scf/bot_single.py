"""AI Code Review Bot - 单文件版（SCF兼容）"""

import sys
import os

# 确保当前目录在sys.path中
if "." not in sys.path:
    sys.path.insert(0, ".")

# === config.py ===
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


# === reviewer.py ===
"""代码审查引擎"""

import re
import logging
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReviewComment:
    """审查评论"""
    path: str
    line: int
    body: str
    severity: str = "info"  # info, warning, error


@dataclass
class ReviewResult:
    """审查结果"""
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)
    approved: bool = True
    score: int = 100  # 0-100


class AIReviewer:
    """AI增强审查 - 调用DeepSeek API"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._http = httpx.Client(timeout=60)

    def review_diff(self, diff: str, rule_result: ReviewResult) -> Optional[str]:
        """用AI审查diff，返回AI审查意见"""
        if not self.config.ai_api_key:
            return None

        # 截断过长的diff
        max_chars = 8000
        if len(diff) > max_chars:
            diff = diff[:max_chars] + "\n... (diff已截断)"

        # 构建规则引擎结果摘要
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
[如果有新发现的问题，逐条列出，每条包含：文件路径、问题描述、建议修改]
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

    # 危险函数检测
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

    # 通用代码质量规则
    QUALITY_RULES = [
        # (pattern, message, severity)
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

        # 解析diff
        files = self._parse_diff(diff)

        for file_path, file_diff in files.items():
            # 检测语言
            detected_lang = language or self._detect_language(file_path)

            # 规则1: 危险函数检测
            lang_dangers = self.DANGEROUS_FUNCTIONS.get(detected_lang, [])
            for line_num, line_content in file_diff.added_lines.items():
                for pattern, message in lang_dangers:
                    if pattern in line_content:
                        comments.append(ReviewComment(
                            path=file_path,
                            line=line_num,
                            body=f"⚠️ **安全警告**: {message}",
                            severity="error",
                        ))
                        score -= 15

            # 规则2: 通用质量规则
            for line_num, line_content in file_diff.added_lines.items():
                for pattern, message, severity in self.QUALITY_RULES:
                    if re.search(pattern, line_content, re.IGNORECASE):
                        comments.append(ReviewComment(
                            path=file_path,
                            line=line_num,
                            body=f"{'🔴' if severity == 'error' else '🟡' if severity == 'warning' else 'ℹ️'} {message}",
                            severity=severity,
                        ))
                        if severity == "error":
                            score -= 10
                        elif severity == "warning":
                            score -= 5

        # 生成摘要
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
            comments=comments,
            approved=approved,
            score=score,
        )

    def _parse_diff(self, diff: str) -> dict[str, "FileDiff"]:
        """解析diff为文件列表"""
        files: dict[str, FileDiff] = {}
        current_file = None
        current_line = 0

        for line in diff.split("\n"):
            if line.startswith("diff --git"):
                # 新文件开始
                match = re.search(r"b/(.+)", line)
                if match:
                    current_file = match.group(1)
                    files[current_file] = FileDiff(path=current_file)
            elif line.startswith("@@"):
                # 行号信息
                match = re.search(r"\+(\d+)", line)
                if match and current_file:
                    current_line = int(match.group(1)) - 1
            elif line.startswith("+") and not line.startswith("+++"):
                if current_file and current_file in files:
                    current_line += 1
                    files[current_file].added_lines[current_line] = line[1:]
            elif not line.startswith("-") and not line.startswith("\\"):
                current_line += 1

        return files

    def _detect_language(self, file_path: str) -> str:
        """根据文件扩展名检测语言"""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "javascript",
            ".jsx": "javascript",
            ".tsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
        }
        for ext, lang in ext_map.items():
            if file_path.endswith(ext):
                return lang
        return "unknown"


@dataclass
class FileDiff:
    """文件diff"""
    path: str
    added_lines: dict[int, str] = field(default_factory=dict)


# === github.py ===
"""GitHub API客户端 - 使用PAT认证"""

import httpx
import logging
from typing import Any, Optional

# BotConfig defined above

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


# === webhook.py ===
"""Webhook处理服务"""

import hmac
import hashlib
import json
import logging
from typing import Any, Optional

# BotConfig defined above
# GitHubClient defined above
# CodeReviewEngine defined above

logger = logging.getLogger(__name__)


class WebhookHandler:
    """GitHub Webhook处理器"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.github = GitHubClient(config)
        self.reviewer = CodeReviewEngine()
        self.ai_reviewer = AIReviewer(config)

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

            # AI增强审查
            ai_review = self.ai_reviewer.review_diff(diff, result)

            # 提交审查 - summary review（包含规则引擎 + AI审查结果）
            event_type = "COMMENT"
            
            summary_lines = [result.summary]
            if result.comments:
                summary_lines.append("\n---\n**📋 规则引擎审查详情：**\n")
                for i, c in enumerate(result.comments[:20], 1):
                    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(c.severity, "ℹ️")
                    summary_lines.append(f"{severity_emoji} **{c.path}:{c.line}** — {c.body}")
            
            if ai_review:
                summary_lines.append(f"\n---\n{ai_review}")
            
            full_summary = "\n".join(summary_lines)
            
            self.github.create_review(
                owner, repo_name, pr_number,
                body=full_summary,
                event=event_type,
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


# === index.py (main_handler) ===
"""腾讯云SCF入口 - AI Code Review Bot"""

import json
import logging
import traceback

# BotConfig defined above
# WebhookHandler defined above

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
