import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class QuestionType(str, enum.Enum):
    single_choice = "single_choice"
    multiple_choice = "multiple_choice"
    true_false = "true_false"
    short_text = "short_text"
    image_choice = "image_choice"
    matching = "matching"
    sequence = "sequence"
    hotspot = "hotspot"


class DifficultyLevel(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class AttemptStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    finished = "finished"
    timeout = "timeout"
    paused = "paused"
    stopped = "stopped"


class EventType(str, enum.Enum):
    login = "login"
    start_test = "start_test"
    answer_saved = "answer_saved"
    question_changed = "question_changed"
    test_finished = "test_finished"
    connection_lost = "connection_lost"
    reconnect = "reconnect"
    tab_blur = "tab_blur"
    tab_focus = "tab_focus"
    timeout_auto_submit = "timeout_auto_submit"
    pause_test = "pause_test"
    resume_test = "resume_test"
    stop_test = "stop_test"
    question_skipped = "question_skipped"
    question_returned = "question_returned"


# ---------------------------------------------------------------------------
# Teacher
# ---------------------------------------------------------------------------

class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    full_name = Column(String(150), nullable=False)
    hashed_password = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True)
    subject = Column(String(100), nullable=True)
    classes = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    tests = relationship("Test", back_populates="teacher")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=True)
    class_name = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    time_limit_minutes = Column(Integer, nullable=True)
    time_limit_per_question = Column(Integer, nullable=True)
    shuffle_questions = Column(Boolean, default=False)
    shuffle_options = Column(Boolean, default=False)
    show_result_after_finish = Column(Boolean, default=True)
    show_correct_answers = Column(Boolean, default=False)
    is_formative = Column(Boolean, default=False)
    use_fuzzy_matching = Column(Boolean, default=False)
    allow_partial_grading = Column(Boolean, default=False)
    allow_retake = Column(Boolean, default=False)
    max_grade = Column(Integer, default=12)
    random_questions_limit = Column(Integer, nullable=True)
    excluded_topics = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    folder_name = Column(String(255), nullable=True)
    is_archived = Column(Boolean, default=False)

    teacher = relationship("Teacher", back_populates="tests")
    questions = relationship("Question", back_populates="test", cascade="all, delete-orphan", order_by="Question.order_index")
    sessions = relationship("TestSession", back_populates="test", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Question
# ---------------------------------------------------------------------------

class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(Enum(QuestionType), nullable=False, default=QuestionType.single_choice)
    points = Column(Float, nullable=False, default=1.0)
    topic = Column(String(150), nullable=True)
    difficulty = Column(Enum(DifficultyLevel), nullable=True, default=DifficultyLevel.medium)
    explanation = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
    image_url = Column(String(255), nullable=True)

    test = relationship("Test", back_populates="questions")
    options = relationship("AnswerOption", back_populates="question", cascade="all, delete-orphan", order_by="AnswerOption.order_index")
    student_answers = relationship("StudentAnswer", back_populates="question")


# ---------------------------------------------------------------------------
# AnswerOption
# ---------------------------------------------------------------------------

class AnswerOption(Base):
    __tablename__ = "answer_options"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    option_text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False)
    order_index = Column(Integer, nullable=False, default=0)
    image_url = Column(String(255), nullable=True)
    matching_text = Column(String(255), nullable=True)

    question = relationship("Question", back_populates="options")


# ---------------------------------------------------------------------------
# TestSession
# ---------------------------------------------------------------------------

class TestSession(Base):
    __tablename__ = "test_sessions"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("tests.id"), nullable=False)
    access_code = Column(String(20), unique=True, nullable=False, index=True)
    started_at = Column(DateTime, default=datetime.now)
    ended_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)

    test = relationship("Test", back_populates="sessions")
    attempts = relationship("StudentAttempt", back_populates="session", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# StudentAttempt
# ---------------------------------------------------------------------------

class StudentAttempt(Base):
    __tablename__ = "student_attempts"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("test_sessions.id"), nullable=False)
    student_name = Column(String(200), nullable=False)
    started_at = Column(DateTime, default=datetime.now)
    finished_at = Column(DateTime, nullable=True)
    status = Column(Enum(AttemptStatus), default=AttemptStatus.not_started)
    score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    is_archived = Column(Boolean, default=False)

    session = relationship("TestSession", back_populates="attempts")
    answers = relationship("StudentAnswer", back_populates="attempt", cascade="all, delete-orphan")
    event_logs = relationship("EventLog", back_populates="attempt", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# StudentAnswer
# ---------------------------------------------------------------------------

class StudentAnswer(Base):
    __tablename__ = "student_answers"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("student_attempts.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    answer_text = Column(Text, nullable=True)
    selected_options_json = Column(Text, nullable=True)  # JSON list of option ids
    is_correct = Column(Boolean, nullable=True)
    awarded_points = Column(Float, nullable=True, default=0.0)
    answered_at = Column(DateTime, default=datetime.now)

    attempt = relationship("StudentAttempt", back_populates="answers")
    question = relationship("Question", back_populates="student_answers")


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------

class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("student_attempts.id"), nullable=False)
    event_type = Column(Enum(EventType), nullable=False)
    event_time = Column(DateTime, default=datetime.now)
    details = Column(Text, nullable=True)

    attempt = relationship("StudentAttempt", back_populates="event_logs")


# ---------------------------------------------------------------------------
# TeacherLog
# ---------------------------------------------------------------------------

class TeacherLog(Base):
    __tablename__ = "teacher_logs"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True)
    teacher_username = Column(String(50), nullable=False)
    teacher_name = Column(String(150), nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    teacher = relationship("Teacher")

