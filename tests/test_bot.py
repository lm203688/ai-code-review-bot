"""AI Code Review Bot 测试"""

import pytest
import os

os.environ.setdefault("CODE_REVIEW_GITHUB_APP_ID", "test_app")
os.environ.setdefault("CODE_REVIEW_AI_API_KEY", "test_key")

from ai_code_review_bot.config import BotConfig
from ai_code_review_bot.reviewer import CodeReviewEngine, ReviewResult
from ai_code_review_bot.webhook import WebhookHandler


class TestConfig:
    def test_defaults(self):
        config = BotConfig()
        assert config.max_diff_size == 10000
        assert config.review_language == "zh-CN"
        assert config.skip_draft is True
        assert config.auto_approve is False


class TestDiffParser:
    def setup_method(self):
        self.engine = CodeReviewEngine()

    def test_parse_simple_diff(self):
        diff = """diff --git a/main.py b/main.py
@@ -1,3 +1,5 @@
 import os
+import sys
+from pathlib import Path
 def main():
-    pass
+    print("hello")"""

        files = self.engine._parse_diff(diff)
        assert "main.py" in files
        assert len(files["main.py"].added_lines) >= 2

    def test_parse_multi_file_diff(self):
        diff = """diff --git a/a.py b/a.py
@@ -1 +1 @@
-old
+new
diff --git a/b.py b/b.py
@@ -1 +1 @@
-old2
+new2"""

        files = self.engine._parse_diff(diff)
        assert "a.py" in files
        assert "b.py" in files


class TestCodeReview:
    def setup_method(self):
        self.engine = CodeReviewEngine()

    def test_clean_code_passes(self):
        diff = """diff --git a/clean.py b/clean.py
@@ -1 +1 @@
+def hello():
+    return "world"
"""
        result = self.engine.review_diff(diff, "python")
        assert result.score >= 90

    def test_detect_eval(self):
        diff = """diff --git a/danger.py b/danger.py
@@ -1 +1 @@
+result = eval(user_input)
"""
        result = self.engine.review_diff(diff, "python")
        assert any("eval" in c.body for c in result.comments)
        assert result.score < 100

    def test_detect_hardcoded_password(self):
        diff = """diff --git a/config.py b/config.py
@@ -1 +1 @@
+password = "my_secret_123"
"""
        result = self.engine.review_diff(diff, "python")
        assert any("密码" in c.body for c in result.comments)

    def test_detect_console_log(self):
        diff = """diff --git a/app.js b/app.js
@@ -1 +1 @@
+console.log("debug")
"""
        result = self.engine.review_diff(diff, "javascript")
        assert any("console.log" in c.body for c in result.comments)

    def test_detect_empty_catch(self):
        diff = """diff --git a/app.js b/app.js
@@ -1 +1 @@
+try { doSomething() } catch(e) {}
"""
        result = self.engine.review_diff(diff, "javascript")
        assert any("空catch" in c.body for c in result.comments)

    def test_detect_empty_except(self):
        diff = """diff --git a/app.py b/app.py
@@ -1 +1 @@
+try:
+    do_something()
+except:
+    pass
"""
        result = self.engine.review_diff(diff, "python")
        assert any("空except" in c.body for c in result.comments)

    def test_score_decreases_with_issues(self):
        diff = """diff --git a/bad.py b/bad.py
@@ -1 +1 @@
+eval(user_input)
+password = "secret"
"""
        result = self.engine.review_diff(diff, "python")
        assert result.score < 80
        assert result.approved is False


class TestLanguageDetection:
    def setup_method(self):
        self.engine = CodeReviewEngine()

    def test_python(self):
        assert self.engine._detect_language("main.py") == "python"

    def test_javascript(self):
        assert self.engine._detect_language("app.js") == "javascript"

    def test_typescript(self):
        assert self.engine._detect_language("app.ts") == "javascript"

    def test_go(self):
        assert self.engine._detect_language("main.go") == "go"

    def test_unknown(self):
        assert self.engine._detect_language("Makefile") == "unknown"


class TestWebhookSignature:
    def test_verify_without_secret(self):
        config = BotConfig()
        handler = WebhookHandler(config)
        assert handler.verify_signature(b"test", "any") is True

    def test_verify_with_secret(self):
        import hmac, hashlib
        config = BotConfig(github_webhook_secret="my_secret")
        handler = WebhookHandler(config)
        payload = b'{"test": true}'
        sig = "sha256=" + hmac.new(b"my_secret", payload, hashlib.sha256).hexdigest()
        assert handler.verify_signature(payload, sig) is True
        assert handler.verify_signature(payload, "sha256=wrong") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
