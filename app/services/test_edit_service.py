"""Validate editor input and preserve questions used by existing attempts."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from app import models
from app.services.media_service import canonical_image_url


class EditorMedia(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_url: str | None = Field(default=None, max_length=255)

    @field_validator('image_url', mode='before')
    @classmethod
    def portable_image_url(cls, value):
        return canonical_image_url(value)


class EditorOption(EditorMedia):
    id: int | None = Field(default=None, ge=1)
    option_text: str = ''
    is_correct: bool = False
    order_index: int = Field(default=0, ge=0)
    matching_text: str | None = Field(default=None, max_length=255)


class EditorQuestion(EditorMedia):
    id: int | None = Field(default=None, ge=1)
    question_text: str = ''
    question_type: models.QuestionType = models.QuestionType.single_choice
    points: float = Field(default=1, gt=0, allow_inf_nan=False)
    topic: str | None = Field(default=None, max_length=150)
    difficulty: models.DifficultyLevel | None = models.DifficultyLevel.medium
    explanation: str | None = None
    order_index: int = Field(default=0, ge=0)

    options: list[EditorOption] = Field(default_factory=list)


def validate_editor_questions(questions):
    try:
        rows = TypeAdapter(list[EditorQuestion]).validate_python(questions)
    except ValidationError as error:
        field = '.'.join(str(part) for part in error.errors()[0]['loc'])
        raise HTTPException(422, 'Некоректні дані питань або варіантів відповіді' + (f' ({field})' if field else '')) from None
    return [row.model_dump(mode='json', exclude_unset=True) for row in rows]


def preserve_assigned_questions(db, test, questions):
    assigned = {row[0] for row in db.query(models.StudentAnswer.question_id).join(models.Question).filter(
        models.Question.test_id == test.id,
    ).distinct()}
    proposed = {row.get('id'): row for row in questions if row.get('id')}
    if assigned - proposed.keys():
        raise HTTPException(409, 'Не можна видалити питання, використані у спробах учнів. Створіть окремий тест для нового набору питань.')
    for question in test.questions:
        if question.id not in assigned:
            continue
        row = proposed[question.id]
        fields = ('question_text', 'question_type', 'points', 'topic', 'explanation', 'image_url')
        def comparable(field, value):
            return (canonical_image_url(value) if field == 'image_url' else value) or None
        changed = any(comparable(field, row.get(field)) != comparable(field, getattr(question, field)) for field in fields)
        options = {option.get('id'): option for option in row.get('options', [])}
        changed |= set(options) != {option.id for option in question.options}
        for option in question.options:
            incoming = options.get(option.id, {})
            option_fields = ('option_text', 'is_correct', 'matching_text', 'image_url')
            if question.question_type == models.QuestionType.sequence:
                option_fields += ('order_index',)
            changed |= any(comparable(field, incoming.get(field)) != comparable(field, getattr(option, field)) for field in option_fields)
        if changed:
            raise HTTPException(409, 'Не можна змінювати зміст або варіанти питання, використані у спробах учнів. Створіть окремий тест для змінених питань.')
