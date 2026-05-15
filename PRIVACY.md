# Privacy Policy for AI Code Review Bot

**Last updated: May 15, 2026**

## What data we collect

AI Code Review Bot processes the following data when you use our service:

- **Pull Request diffs**: The code changes in your pull requests are sent to our server for analysis
- **Repository metadata**: Repository name, owner, and PR number (needed to post review comments)
- **Usage data**: Number of reviews per month (for free tier limit enforcement)

## How we use your data

- Code diffs are analyzed by our rule engine and AI service (DeepSeek) to generate review comments
- Diffs are **not stored** after the review is complete
- We do **not** train AI models on your code
- Usage data is stored only as a count (no code content)

## Third-party services

- **DeepSeek API**: Code diffs may be sent to DeepSeek for AI-powered review analysis. See [DeepSeek's privacy policy](https://www.deepseek.com/privacy)
- **Tencent Cloud SCF**: Our service runs on Tencent Cloud Serverless Functions. See [Tencent Cloud's privacy policy](https://www.tencentcloud.com/document/product/301/17345)

## Data retention

- Code diffs are processed in memory and **not persisted** to any database
- Usage counts are stored in temporary function storage and reset monthly
- We do not retain any of your source code

## Security

- All webhook communications are verified via HMAC-SHA256 signatures
- API tokens are stored as encrypted environment variables
- We follow the principle of least privilege for GitHub App permissions

## Your rights

- You can uninstall the app at any time from your GitHub settings
- Uninstalling revokes all access tokens immediately
- You can request data deletion by contacting us

## Contact

For privacy questions, open an issue at: https://github.com/lm203688/ai-code-review-bot/issues
