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
