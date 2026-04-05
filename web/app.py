"""Compatibility entrypoint for the Flask web app."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from web.app_factory import create_app
from web.core.shared import *  # noqa: F403
from web.services.runtime import *  # noqa: F403
from web.services.content import *  # noqa: F403
from web.services.reference import *  # noqa: F403
from web.services.llm import *  # noqa: F403

app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
