"""统一配置：从 .env 读取全部外部凭证与端点（.env 不进 git）。"""
import os
from pathlib import Path

def _load() -> None:
    env = Path(__file__).parent / ".env"
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load()
BAILIAN_API_KEY = os.environ["BAILIAN_API_KEY"]
BAILIAN_BASE_URL = os.environ["BAILIAN_BASE_URL"]
OZON_CLIENT_ID = os.environ["OZON_CLIENT_ID"]
OZON_API_KEY = os.environ["OZON_API_KEY"]
NEXSCOPE_API_KEY = os.environ["NEXSCOPE_API_KEY"]
