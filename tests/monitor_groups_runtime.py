"""Long synthetic monitor; refuses every database except the disposable UI DB."""
import os
from datetime import datetime, timedelta
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable monitor database is allowed')

from app import crud, models
from app.database import SessionLocal

with SessionLocal() as db:
    owner = crud.get_teacher_by_username(db, 'teacher_ui')
    test = crud.create_test(db, owner.id, {'title': 'Повторні спроби та прокручування',
        'questions': [{'question_text': '2 + 2?', 'options': [{'option_text': '4', 'is_correct': True}]}]})
    session = crud.create_session(db, test.id, 'SCROLL')
    for number in range(35):
        for repeat in range(2 + (number == 0)):
            attempt = crud.create_attempt(db, session.id, f'Учень {number:02}', outside_student_id=f'synthetic-scroll-{number}')
            attempt.started_at = datetime.now() - timedelta(minutes=20 - repeat)
            attempt.status = models.AttemptStatus.finished
            attempt.finished_at = attempt.started_at + timedelta(minutes=1)
            attempt.score, attempt.max_score = repeat + 1, 4
    active = crud.create_attempt(db, session.id, 'Учень 00', outside_student_id='synthetic-scroll-0')
    active.status = models.AttemptStatus.in_progress
    active.started_at = datetime.now()
    db.commit()
    print(f'Synthetic long monitor: session={session.id}, active_attempt={active.id}')
