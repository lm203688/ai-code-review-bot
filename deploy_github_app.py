"""部署AI Code Review Bot v2 (GitHub App) 到腾讯云SCF

使用Layer分离依赖，函数URL替代API网关
"""

import base64
import json
import os
import sys
import time

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.scf.v20180416 import scf_client, models

# 配置
SECRET_ID = os.environ["TENCENT_SECRET_ID"]
SECRET_KEY = os.environ["TENCENT_SECRET_KEY"]
REGION = os.environ.get("TENCENT_REGION", "ap-guangzhou")
FUNCTION_NAME = "ai-code-review-bot"
NAMESPACE = "default"
LAYER_NAME = "ai-code-review-deps"

# 文件路径
LAYER_ZIP = "/tmp/layer_github_app.zip"
FUNCTION_ZIP = "/tmp/function_github_app.zip"


def get_client():
    cred = credential.Credential(SECRET_ID, SECRET_KEY)
    httpProfile = HttpProfile()
    httpProfile.endpoint = "scf.tencentcloudapi.com"
    clientProfile = ClientProfile()
    clientProfile.httpProfile = httpProfile
    return scf_client.ScfClient(cred, REGION, clientProfile)


def deploy_layer(client):
    """部署Layer（依赖包）"""
    with open(LAYER_ZIP, "rb") as f:
        zip_bytes = f.read()
    zip_b64 = base64.b64encode(zip_bytes).decode()

    # 先尝试删除旧版本
    for version in range(1, 10):
        try:
            req = models.DeleteLayerVersionRequest()
            req.from_json_string(json.dumps({
                "LayerName": LAYER_NAME,
                "LayerVersionNumber": version,
            }))
            client.DeleteLayerVersion(req)
        except Exception:
            break

    # 发布新Layer
    params = {
        "LayerName": LAYER_NAME,
        "CompatibleRuntimes": ["Python3.9", "Python3.10", "Python3.12"],
        "Content": {
            "ZipFile": zip_b64,
        },
        "Description": "AI Code Review Bot dependencies (httpx, rsa, pyasn1)",
    }
    req = models.PublishLayerVersionRequest()
    req.from_json_string(json.dumps(params))
    resp = client.PublishLayerVersion(req)
    data = json.loads(resp.to_json_string())
    version = data.get("LayerVersionNumber", 1)
    print(f"Layer发布成功: {LAYER_NAME} v{version}")
    return version


def deploy_function(client, layer_version):
    """部署SCF函数"""
    with open(FUNCTION_ZIP, "rb") as f:
        zip_bytes = f.read()
    zip_b64 = base64.b64encode(zip_bytes).decode()

    # 检查函数是否已存在
    exists = False
    req = models.GetFunctionRequest()
    req.from_json_string(json.dumps({
        "FunctionName": FUNCTION_NAME,
        "Namespace": NAMESPACE,
    }))
    try:
        client.GetFunction(req)
        exists = True
    except Exception:
        pass

    env_vars = [
        {"Key": "GITHUB_APP_ID", "Value": os.environ.get("GITHUB_APP_ID", "")},
        {"Key": "GITHUB_APP_PRIVATE_KEY", "Value": os.environ.get("GITHUB_APP_PRIVATE_KEY", "")},
        {"Key": "GITHUB_WEBHOOK_SECRET", "Value": os.environ.get("GITHUB_WEBHOOK_SECRET", "")},
        {"Key": "CODE_REVIEW_AI_API_ENDPOINT", "Value": os.environ.get("CODE_REVIEW_AI_API_ENDPOINT", "https://api.deepseek.com")},
        {"Key": "CODE_REVIEW_AI_API_KEY", "Value": os.environ.get("CODE_REVIEW_AI_API_KEY", "")},
        {"Key": "CODE_REVIEW_AI_MODEL", "Value": os.environ.get("CODE_REVIEW_AI_MODEL", "deepseek-chat")},
        {"Key": "FREE_MONTHLY_LIMIT", "Value": os.environ.get("FREE_MONTHLY_LIMIT", "5")},
    ]

    if exists:
        # 更新函数代码
        print("函数已存在，更新代码...")
        params = {
            "FunctionName": FUNCTION_NAME,
            "Namespace": NAMESPACE,
            "Code": {"ZipFile": zip_b64},
            "Handler": "bot_github_app.main_handler",
        }
        req = models.UpdateFunctionCodeRequest()
        req.from_json_string(json.dumps(params))
        client.UpdateFunctionCode(req)
        print("函数代码更新成功")

        # 更新函数配置
        time.sleep(2)
        params = {
            "FunctionName": FUNCTION_NAME,
            "Namespace": NAMESPACE,
            "Handler": "bot_github_app.main_handler",
            "Timeout": 60,
            "MemorySize": 256,
            "Environment": {"Variables": env_vars},
            "Layers": [{"LayerName": LAYER_NAME, "LayerVersion": layer_version}],
        }
        req = models.UpdateFunctionConfigurationRequest()
        req.from_json_string(json.dumps(params))
        client.UpdateFunctionConfiguration(req)
        print("函数配置更新成功")
    else:
        # 创建新函数
        print("创建新函数...")
        params = {
            "FunctionName": FUNCTION_NAME,
            "Namespace": NAMESPACE,
            "Code": {"ZipFile": zip_b64},
            "Handler": "bot_github_app.main_handler",
            "Runtime": "Python3.9",
            "Timeout": 60,
            "MemorySize": 256,
            "Description": "AI Code Review Bot v2 - GitHub App",
            "Environment": {"Variables": env_vars},
            "Layers": [{"LayerName": LAYER_NAME, "LayerVersion": layer_version}],
        }
        req = models.CreateFunctionRequest()
        req.from_json_string(json.dumps(params))
        resp = client.CreateFunction(req)
        print(f"函数创建成功: {resp.to_json_string()[:200]}")

    return exists


def enable_function_url(client):
    """启用函数URL（替代API网关）"""
    time.sleep(3)
    try:
        params = {
            "FunctionName": FUNCTION_NAME,
            "Namespace": NAMESPACE,
            "AuthType": "NONE",
        }
        req = models.CreateFunctionUrlRequest()
        req.from_json_string(json.dumps(params))
        resp = client.CreateFunctionUrl(req)
        data = json.loads(resp.to_json_string())
        url = data.get("Url", "")
        print(f"函数URL: {url}")
        return url
    except Exception as e:
        if "already" in str(e) or "已存在" in str(e):
            # 获取现有URL
            try:
                req = models.GetFunctionUrlRequest()
                req.from_json_string(json.dumps({
                    "FunctionName": FUNCTION_NAME,
                    "Namespace": NAMESPACE,
                }))
                resp = client.GetFunctionUrl(req)
                data = json.loads(resp.to_json_string())
                url = data.get("Url", "")
                print(f"函数URL(已有): {url}")
                return url
            except Exception:
                pass
        print(f"函数URL创建失败: {e}")
        print("请在腾讯云控制台手动开启函数URL")
        return None


def main():
    client = get_client()

    # 1. 部署Layer
    print("=" * 50)
    print("Step 1: 部署Layer...")
    layer_version = deploy_layer(client)

    # 2. 部署函数
    print("=" * 50)
    print("Step 2: 部署函数...")
    is_update = deploy_function(client, layer_version)

    # 3. 启用函数URL
    print("=" * 50)
    print("Step 3: 启用函数URL...")
    url = enable_function_url(client)

    print("=" * 50)
    if url:
        print(f"\n✅ 部署成功！")
        print(f"Webhook URL: {url}")
        print(f"\n下一步：在GitHub创建App，Webhook URL填: {url}")
    else:
        print(f"\n✅ 代码已部署！")
        print("请在腾讯云控制台开启函数URL，然后配置GitHub App")


if __name__ == "__main__":
    main()
