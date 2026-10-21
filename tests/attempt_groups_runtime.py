"""Extra synthetic scenarios in the disposable runtime database only."""
import json
import os
from datetime import datetime,timedelta
from sqlalchemy.engine import make_url

url=make_url(os.environ['DATABASE_URL'])
if (url.host,url.database,url.username)!=('postgres','schooltest_runtime_check','runtime_check'):
    raise RuntimeError('Only the disposable attempt-group database is allowed')

from app import crud,models
from app.database import SessionLocal
from app.services.test_service import get_attempt_questions

with SessionLocal() as db:
    owner=crud.get_teacher_by_username(db,'teacher_ui')
    session=db.query(models.TestSession).filter_by(access_code='UI2026').one()
    first=db.get(models.StudentAttempt,1)
    active=crud.start_attempt(db,crud.create_attempt(db,session.id,'Поточна третя спроба',roster_student_id=first.roster_student_id))
    get_attempt_questions(db,active)
    for name, identity in [('Іван  Іван',{}),('іван іван',{}),
        (first.student_name,{'roster_student_id':2}),
        (first.student_name,{'outside_student_id':'another-browser'})]:
        a=crud.create_attempt(db,session.id,name,**identity)
        a.status,a.score,a.max_score,a.finished_at=models.AttemptStatus.finished,1,3,datetime.now()
    dynamic_test=crud.create_test(db,owner.id,{'title':'Динамічний вступ','questions':[{'question_text':'2+2?',
        'options':[{'option_text':'4','is_correct':True},{'option_text':'5'}]}]})
    dynamic=crud.create_session(db,dynamic_test.id,'GROUPS2')
    for code,active_state,age in [('ZZ9000',True,30),('AA1000',True,60),('CC1000',False,-30)]:
        s=crud.create_session(db,session.test_id,code)
        s.is_active,s.started_at=active_state,datetime.now()-timedelta(minutes=age)
    db.commit()
    print(json.dumps({'active_attempt':active.id,'dynamic_session':dynamic.id}))
