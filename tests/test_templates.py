from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.catalog.presentation import track_token
from app.core.time import display_date


def test_all_templates_compile_and_autoescape() -> None:
    root = Path(__file__).parents[1] / "app" / "templates"
    env = Environment(loader=FileSystemLoader(root), autoescape=select_autoescape(("html",)))
    env.filters.update(
        display_time=lambda value: str(value or ""), display_datetime=lambda value: str(value or ""),
        display_date=display_date,
        track_token=track_token,
    )
    for path in root.glob("*.html"):
        env.get_template(path.name)
    template = env.from_string("{{ value }}")
    assert template.render(value="<script>alert(1)</script>").startswith("&lt;script&gt;")
