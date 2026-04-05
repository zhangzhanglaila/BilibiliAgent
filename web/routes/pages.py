from __future__ import annotations

from flask import Blueprint, render_template

from web.services.runtime import build_runtime_payload

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def index():
    return render_template("index.html", initial_runtime=build_runtime_payload())
