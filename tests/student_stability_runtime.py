"""Synthetic scenarios, exclusively in tests/compose.runtime.yaml."""
import json
import os
import sys

from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if (url.host, url.database, url.username) != ('postgres', 'schooltest_runtime_check', 'runtime_check'):
    raise RuntimeError('Only the disposable student-stability database is allowed')

from app import crud, models
from app.database import SessionLocal
from app.services.test_service import get_attempt_questions
from app.services.testing_policy import violation_count


def seed():
    with SessionLocal() as db:
        owner = crud.create_teacher(db, 'stability_check', 'Перевірка стабільності', 'synthetic-stability-password')
        group = models.RosterClass(name='11-А', grade_level=11, letter='А')
        db.add(group); db.flush()
        db.add_all([models.RosterStudent(class_id=group.id, first_name='Учень', last_name='Зі списку'),
                    models.RosterStudent(class_id=group.id, first_name='Учениця', last_name='Друга')])
        db.commit()
        test = crud.create_test(db, owner.id, {
            'title': 'Урок_1 Історія розвитку інформаційних технологій',
            'description': 'Тести інформатика 9 клас Морзе 2026', 'subject': 'Демонстрація',
            'time_limit_minutes': 45, 'time_limit_per_question': 60, 'allow_retake': True,
            'show_result_after_finish': True,
            'questions': [{'question_text': f'Питання {i + 1}: оберіть відповідь.',
                'question_type': 'single_choice', 'order_index': i,
                'options': [{'option_text': 'Правильно', 'is_correct': True}, {'option_text': 'Неправильно'}]}
                for i in range(20)],
        })
        manual = crud.create_session(db, test.id, 'STAB01')
        roster = crud.create_session(db, test.id, 'STAB02')
        roster.roster_class_id = group.id
        db.commit()
        unaffected = crud.start_attempt(db, crud.create_attempt(db, manual.id, 'Не зачіпати'))
        get_attempt_questions(db, unaffected)
    print('Seeded manual/roster entry, 20 questions, both timers, and unaffected attempt')


def verify():
    expected = {'Три попередження': ('stopped', 3, 2),
        'Чернетка перед зупинкою': ('stopped', 0, 0),
        'Відхилена чернетка': ('stopped', 3, 0),
        'Попередження офлайн': ('stopped', 3, 1),
        'Відновлення мережі': ('finished', 0, 1),
        'Пауза й мережа': ('finished', 0, 1)}
    result = {}
    with SessionLocal() as db:
        for name, (status, warnings, saved) in expected.items():
            a = db.query(models.StudentAttempt).filter_by(student_name=name).one()
            answered = [x for x in a.answers if x.answer_text is not None or x.selected_options_json is not None]
            assert (a.status.value, violation_count(db, a.id), len(answered)) == (status, warnings, saved), name
            if status == 'stopped':
                assert db.query(models.EventLog).filter_by(attempt_id=a.id, event_type=models.EventType.stop_test).count() == 1
            result[name] = {'status': status, 'warnings': warnings, 'confirmed_answers': len(answered), 'score': a.score}
        assert db.query(models.StudentAttempt).filter_by(student_name='Не зачіпати').one().status == models.AttemptStatus.in_progress
    print(json.dumps(result, ensure_ascii=False, indent=2))


def reset():
    with SessionLocal() as db:
        attempts = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).join(models.Teacher).filter(
            models.Teacher.username == 'stability_check', models.StudentAttempt.student_name != 'Не зачіпати').all()
        for attempt in attempts:
            db.delete(attempt)
        db.commit()


{'seed': seed, 'verify': verify, 'reset': reset}[sys.argv[1]]()
