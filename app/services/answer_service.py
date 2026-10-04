"""Validate submissions before persisting any part of an answer."""

import json
import math

from fastapi import HTTPException

from app import models


def validate_submission(question, body):
    text = body.get("answer_text")
    selected = body.get("selected_options")
    valid = text is None or (isinstance(text, str) and len(text) <= 2000)
    if not valid:
        raise HTTPException(422, "Відповідь має бути текстом до 2000 символів")
    # A question left unanswered by the timer has an explicit empty answer.
    if text == "" and selected is None:
        return text, None
    options = {option.id for option in question.options}
    kind = question.question_type
    if kind == models.QuestionType.short_text:
        valid = isinstance(text, str) and selected is None
    elif kind == models.QuestionType.matching:
        valid = text is None and isinstance(selected, dict) and all(
            key in {str(value) for value in options}
            and isinstance(value, str) and len(value) <= 1000
            for key, value in selected.items()
        )
        if valid:
            pool = {option.matching_text for option in question.options}
            valid = all(value in pool for value in selected.values())
    elif kind == models.QuestionType.hotspot:
        valid = text is None and isinstance(selected, dict) and set(selected) == {"x", "y"}
        if valid:
            valid = all(
                type(value) in (int, float) and math.isfinite(value) and value >= 0
                for value in selected.values()
            )
    else:
        valid = text is None and isinstance(selected, list) and all(
            type(value) is int and value in options for value in selected
        )
        if valid:
            valid = len(selected) == len(set(selected))
            if kind in (models.QuestionType.single_choice, models.QuestionType.true_false, models.QuestionType.image_choice):
                valid = valid and len(selected) == 1
            if kind == models.QuestionType.sequence:
                valid = valid and set(selected) == options
    if not valid:
        raise HTTPException(422, "Невірний формат відповіді або варіанти з іншого питання")
    return text, json.dumps(selected, ensure_ascii=False, sort_keys=True) if selected is not None else None


def same_submission(answer, text, selected_json):
    if answer.answer_text != text:
        return False
    existing = json.loads(answer.selected_options_json) if answer.selected_options_json is not None else None
    submitted = json.loads(selected_json) if selected_json is not None else None
    if answer.question.question_type in (
        models.QuestionType.single_choice, models.QuestionType.multiple_choice,
        models.QuestionType.true_false, models.QuestionType.image_choice,
    ) and isinstance(existing, list) and isinstance(submitted, list):
        return set(existing) == set(submitted)
    return existing == submitted
