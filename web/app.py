"""Compatibility entrypoint for the Flask web app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 在所有第三方库导入之前清除系统代理环境变量，防止 Windows 代理污染 HTTP 请求。
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY"):
    os.environ.pop(_proxy_key, None)

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 强制 requests 库忽略系统代理（包括 Windows 注册表代理）
import requests as _requests
_original_session_init = _requests.Session.__init__
def _patched_session_init(self, *args, **kwargs):
    _original_session_init(self, *args, **kwargs)
    self.trust_env = False
_requests.Session.__init__ = _patched_session_init

from web.app_factory import create_app
from web.core.shared import *  # noqa: F403
from web.services.runtime import *  # noqa: F403
from web.services.content import *  # noqa: F403
from web.services.reference import *  # noqa: F403
from web.services.llm import *  # noqa: F403

app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
