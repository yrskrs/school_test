"""Prepare class-label browser checks in the explicitly disposable DB only."""
import os
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable class-label database is allowed')

from app import crud, models
from app.database import SessionLocal
from app.services.session_service import start_new_session

with SessionLocal() as db:
    teacher = crud.get_teacher_by_username(db, 'teacher_ui')
    group = models.RosterClass(name='9-Г', grade_level=9, letter='Г', active=True)
    db.add(group); db.flush()
    db.add(models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Синтетичний'))
    db.commit()
    test = crud.create_test(db, teacher.id, {'title': 'Визначення класу без тире', 'class_name': '9Г',
        'questions': [{'question_text': '2 + 2?', 'options': [{'option_text': '4', 'is_correct': True}]}]})
    session = start_new_session(db, test.id)
    session.access_code = 'CLASS9'; db.commit()
    print('Synthetic 9Г session has the stable 9-Г class ID')
