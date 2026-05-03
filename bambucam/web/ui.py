"""UI routes — serves the single-page WebUI."""

from flask import Blueprint, render_template

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
def index():
    return render_template("index.html")


@ui_bp.get("/health")
def health():
    from flask import jsonify

    return jsonify({"status": "ok"})
