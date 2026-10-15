"""Render the self-contained reviewer page."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent


def render_review(context: dict, out_path: Path, *, three_url: str) -> None:
    env = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape(["html"]))
    template = env.get_template("template.html")
    css = (HERE / "style.css").read_text()
    html = template.render(**context, css=css, three_url=three_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
