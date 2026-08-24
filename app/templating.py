"""
Спільний екземпляр Jinja2Templates з кастомними фільтрами.
Використовується в усіх маршрутизаторах.
"""
import json

from jinja2 import Environment, FileSystemLoader
from starlette.templating import Jinja2Templates

from app.config import settings


def _from_json(s):
    """Jinja2-фільтр: розбирає JSON-рядок у Python-об'єкт."""
    if not s:
        return []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


def _scale_grade(attempt):
    """Jinja2-фільтр: обчислює оцінку по заданій макс. шкалі (напр. 12 балів)."""
    if getattr(attempt, "score", None) is None or getattr(attempt, "max_score", None) is None:
        return "—"
    if attempt.max_score <= 0:
        return "0"
    try:
        max_grade = attempt.session.test.max_grade or 12
        return str(round((attempt.score / attempt.max_score) * max_grade))
    except AttributeError:
        return "—"


templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)
templates.env.filters["from_json"] = _from_json
templates.env.filters["scale_grade"] = _scale_grade
