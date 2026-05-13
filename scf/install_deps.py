"""SCF依赖安装脚本 - 在SCF环境中安装所需依赖"""

import subprocess
import sys
import os

def install_dependencies():
    """安装项目依赖到SCF层"""
    deps = [
        "httpx>=0.27.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "PyJWT>=2.8.0",
        "cryptography>=42.0.0",
    ]

    target = os.environ.get("SCF_LAYERS_DIR", "/opt/python")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        *deps,
        "--target", target,
        "--no-cache-dir",
        "-q",
    ])
    print(f"依赖安装完成: {target}")

if __name__ == "__main__":
    install_dependencies()
