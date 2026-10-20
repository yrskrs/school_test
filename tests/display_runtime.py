"""Seed/check the disposable tests/compose.runtime.yaml environment only."""
import json
import os
import sys

from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable display-check PostgreSQL database is allowed')

from app import crud, models
from app.database import SessionLocal


def seed():
    with SessionLocal() as db:
        teacher = crud.create_teacher(db, 'display_check', 'Перевірка інтерфейсу', 'synthetic-display-password')
        test = crud.create_test(db, teacher.id, {
            'title': 'Тест із 200 питань: перевірка вибірки та розміщення довгих заголовків',
            'subject': 'Інформатика', 'time_limit_minutes': 30,
            'random_questions_limit': 25, 'excluded_topics': '["Виключена тема"]',
            'show_result_after_finish': True,
            'questions': [{'question_text': f'Питання {i + 1}. Оберіть правильну відповідь. ' + 'ДовгийТекст' * 18,
                'question_type': 'single_choice', 'order_index': i,
                'topic': 'Виключена тема' if i < 20 else 'Основна тема',
                'options': [{'option_text': 'Правильно', 'is_correct': True}, {'option_text': 'Неправильно'}]}
                for i in range(200)]})
        crud.create_session(db, test.id, 'VIEW01')
    print('Seeded isolated PostgreSQL: 200 questions, 20 excluded, 25 assigned')


def verify():
    with SessionLocal() as db:
        attempt = db.query(models.StudentAttempt).filter_by(student_name='Перевірка Учень').one()
        assert attempt.status == models.AttemptStatus.finished, attempt.status
        assert len(attempt.answers) == 25
        assert all(a.question.topic != 'Виключена тема' and a.is_correct for a in attempt.answers)
        assert attempt.score == attempt.max_score == 25
        print(json.dumps({'status': attempt.status.value, 'answers': len(attempt.answers),
                          'score': attempt.score, 'max_score': attempt.max_score}))


def reset_attempts():
    with SessionLocal() as db:
        for attempt in db.query(models.StudentAttempt).all():
            db.delete(attempt)
        db.commit()


{'seed': seed, 'verify': verify, 'reset-attempts': reset_attempts}[sys.argv[1]]()
