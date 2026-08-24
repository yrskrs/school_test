from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models import AttemptStatus, DifficultyLevel, EventType, QuestionType


# ---------------------------------------------------------------------------
# AnswerOption
# ---------------------------------------------------------------------------

class AnswerOptionBase(BaseModel):
    option_text: str
    is_correct: bool = False
    order_index: int = 0


class AnswerOptionCreate(AnswerOptionBase):
    pass


class AnswerOptionOut(AnswerOptionBase):
    id: int
    question_id: int

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Question
# ---------------------------------------------------------------------------

class QuestionBase(BaseModel):
    question_text: str
    question_type: QuestionType = QuestionType.single_choice
    points: float = 1.0
    topic: Optional[str] = None
    difficulty: Optional[DifficultyLevel] = DifficultyLevel.medium
    explanation: Optional[str] = None
    order_index: int = 0


class QuestionCreate(QuestionBase):
    options: List[AnswerOptionCreate] = []


class QuestionOut(QuestionBase):
    id: int
    test_id: int
    options: List[AnswerOptionOut] = []

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestBase(BaseModel):
    title: str
    subject: Optional[str] = None
    class_name: Optional[str] = None
    description: Optional[str] = None
    time_limit_minutes: Optional[int] = None
    shuffle_questions: bool = False
    shuffle_options: bool = False
    show_result_after_finish: bool = True
    show_correct_answers: bool = False
    is_formative: bool = False
    use_fuzzy_matching: bool = False


class TestCreate(TestBase):
    questions: List[QuestionCreate] = []


class TestUpdate(TestBase):
    questions: List[QuestionCreate] = []


class TestOut(TestBase):
    id: int
    teacher_id: int
    created_at: datetime
    questions: List[QuestionOut] = []

    class Config:
        from_attributes = True


class TestListItem(BaseModel):
    id: int
    title: str
    subject: Optional[str]
    class_name: Optional[str]
    time_limit_minutes: Optional[int]
    created_at: datetime
    question_count: int = 0

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# TestSession
# ---------------------------------------------------------------------------

class TestSessionCreate(BaseModel):
    test_id: int


class TestSessionOut(BaseModel):
    id: int
    test_id: int
    access_code: str
    started_at: datetime
    ended_at: Optional[datetime]
    is_active: bool
    test_title: Optional[str] = None
    attempt_count: int = 0

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# StudentAttempt
# ---------------------------------------------------------------------------

class AttemptCreate(BaseModel):
    session_id: int
    student_name: str


class AttemptOut(BaseModel):
    id: int
    session_id: int
    student_name: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: AttemptStatus
    score: Optional[float]
    max_score: Optional[float]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# StudentAnswer
# ---------------------------------------------------------------------------

class SaveAnswerRequest(BaseModel):
    question_id: int
    answer_text: Optional[str] = None
    selected_options: Optional[List[int]] = None


class StudentAnswerOut(BaseModel):
    id: int
    attempt_id: int
    question_id: int
    answer_text: Optional[str]
    selected_options_json: Optional[str]
    is_correct: Optional[bool]
    awarded_points: Optional[float]
    answered_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------

class EventLogCreate(BaseModel):
    attempt_id: int
    event_type: EventType
    details: Optional[str] = None


class EventLogOut(BaseModel):
    id: int
    attempt_id: int
    event_type: EventType
    event_time: datetime
    details: Optional[str]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Teacher / Auth
# ---------------------------------------------------------------------------

class TeacherCreate(BaseModel):
    username: str
    full_name: str
    password: str


class TeacherOut(BaseModel):
    id: int
    username: str
    full_name: str
    is_active: bool

    class Config:
        from_attributes = True


class LoginForm(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# API responses
# ---------------------------------------------------------------------------

class SessionStatusResponse(BaseModel):
    session_id: int
    is_active: bool
    test_title: str
    attempts: List[AttemptOut]


class MessageResponse(BaseModel):
    message: str


class ImportTestRequest(BaseModel):
    json_data: str
