from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify

from web.routes.api import api_bp
from web.routes.pages import pages_bp


# 创建并配置Flask应用实例，注册蓝图、错误处理器，返回完整的Web应用。
def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    @app.errorhandler(Exception)
    def handle_error(exc):
        return jsonify({"success": False, "error": str(exc)}), 500

    return app
