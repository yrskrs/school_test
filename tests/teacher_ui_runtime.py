"""Synthetic teacher workspace, only in tests/compose.runtime.yaml."""
import hashlib
import json
import os
from datetime import datetime

from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable teacher UI database is allowed')

from app import crud, models
from app.database import SessionLocal
from app.services.roster_backend import operate


with SessionLocal() as db:
    admin = crud.create_teacher(db, 'admin', 'Адміністратор перевірки інтерфейсу', 'synthetic-ui-password')
    teacher = crud.create_teacher(db, 'teacher_ui', 'Учитель із довгим прізвищем для перевірки розміщення', 'synthetic-ui-password')
    crud.create_teacher(db, 'empty_ui', 'Учитель без тестів', 'synthetic-ui-password')
    teacher.subject, teacher.classes = 'Інформатика', '7-А, 9-Б'
    group = models.RosterClass(name='7-А', grade_level=7, letter='А')
    db.add(group)
    db.flush()
    students = [models.RosterStudent(class_id=group.id, first_name='Учень', last_name='ДовгеПрізвище' * 4),
                models.RosterStudent(class_id=group.id, first_name='Олена', last_name='Перевірка')]
    db.add_all(students)
    db.commit()
    test = crud.create_test(db, teacher.id, {
        'title': 'Контрольна робота — ' + 'ДовгаНазваБезПробілів' * 9,
        'subject': 'Інформатика', 'class_name': '7-А', 'time_limit_minutes': 30,
        'show_result_after_finish': True,
        'questions': [{'question_text': f'Питання {i + 1}. ' + 'ДовгийТекст' * 16,
                       'question_type': 'single_choice', 'order_index': i,
                       'options': [{'option_text': 'Правильна відповідь', 'is_correct': True},
                                   {'option_text': 'Інша відповідь'}]} for i in range(6)]})
    session = crud.create_session(db, test.id, 'UI2026', roster_class_id=group.id)
    finished = crud.create_attempt(db, session.id, students[0].full_name(), roster_student_id=students[0].id)
    finished.status, finished.score, finished.max_score = models.AttemptStatus.finished, 6, 6
    finished.started_at = finished.finished_at = datetime.now()
    for q in test.questions:
        db.add(models.StudentAnswer(attempt_id=finished.id, question_id=q.id,
            selected_options_json=json.dumps([q.options[0].id]), is_correct=True, awarded_points=1))
    for state in [models.AttemptStatus.in_progress, models.AttemptStatus.paused, models.AttemptStatus.not_started]:
        a = crud.create_attempt(db, session.id, f'Учень {state.value}')
        a.status = state
    sibling = crud.create_attempt(db, session.id, students[0].full_name(), roster_student_id=students[0].id)
    sibling.status, sibling.score, sibling.max_score = models.AttemptStatus.finished, 3, 6
    sibling.finished_at = datetime.now()
    archived_result = crud.create_attempt(db, session.id, 'Окремий архівний учень', outside_student_id='synthetic-ui-outside')
    archived_result.status, archived_result.score, archived_result.max_score = models.AttemptStatus.finished, 2, 6
    archived_result.finished_at, archived_result.is_archived = datetime.now(), True
    archived_test = crud.create_test(db, teacher.id, {'title': 'Архівний тест', 'class_name': '9-Б',
        'questions': [{'question_text': 'Архівне питання', 'options': [{'option_text': 'Так', 'is_correct': True}]}]})
    archived_test.is_archived = True
    closed = crud.create_session(db, archived_test.id, 'UIARCH')
    closed.is_active, closed.is_archived = False, True
    grant = models.GradeExportGrant(teacher_id=teacher.id,
        token_hash=hashlib.sha256(b'synthetic-ui-token').hexdigest())
    db.add(grant)
    db.commit()
    def metadata(engine, store):
        store.meta.setdefault('api_grants_meta', {})[str(grant.id)] = {
            'name': 'Підключення ' + 'ДовгаНазва' * 10,
            'description': 'Синтетичне підключення для перевірки адаптивності'}
    operate(metadata, db)
    print(json.dumps({'test': test.id, 'session': session.id, 'attempt': finished.id}))
