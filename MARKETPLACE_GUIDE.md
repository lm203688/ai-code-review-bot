# AI Code Review Bot - GitHub App 上架指南

## 第一步：创建GitHub App

1. 打开 https://github.com/settings/apps/new
2. 填写以下信息：

### 基本信息
- **GitHub App name**: `AI Code Review Bot`
- **Homepage URL**: `https://github.com/lm203688/ai-code-review-bot`
- **Webhook URL**: `https://1341839497-i3yahuu88j.ap-guangzhou.tencentscf.com`
- **Webhook secret**: 自定义一个随机字符串

### 权限设置
- **Pull requests**: Read and write
- **Issues**: Read and write  
- **Contents**: Read-only
- **Metadata**: Read-only

### 订阅事件
- Pull request
- Installation
- Installation repositories
- Marketplace purchase (上架后)

### 其他
- **Where can this GitHub App be installed?**: Any account
- **Private key**: 创建后生成并下载

## 第二步：配置环境变量

在SCF函数配置中添加：

```
GITHUB_APP_ID=<创建App后获得>
GITHUB_APP_PRIVATE_KEY=<下载的PEM私钥内容>
GITHUB_WEBHOOK_SECRET=<你设置的webhook secret>
CODE_REVIEW_AI_API_KEY=<DeepSeek API Key>
CODE_REVIEW_AI_API_ENDPOINT=https://api.deepseek.com
CODE_REVIEW_AI_MODEL=deepseek-chat
```

## 第三步：部署到SCF

```bash
python3 deploy_github_app.py
```

## 第四步：测试

1. 在测试仓库安装App
2. 创建PR
3. 检查是否收到审查评论

## 第五步：上架GitHub Marketplace

1. 进入App设置 → Marketplace
2. 创建定价计划：
   - Free: $0/月, 5次AI审查
   - Pro: $9.9/月, 无限AI审查
3. 提交审核

### Marketplace审核要求
- ✅ 功能完整可用
- ✅ 有Landing Page
- ✅ 有清晰的定价
- ✅ 有隐私政策
- ✅ 有使用条款
