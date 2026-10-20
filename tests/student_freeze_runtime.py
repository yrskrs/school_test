"""Synthetic mixed-type bank, only for the disposable runtime compose project."""
import json
import os
from pathlib import Path
import struct
import sys

from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
assert (url.host, url.database, url.username) == ('postgres', 'schooltest_runtime_check', 'runtime_check')

from app import crud, models
from app.database import SessionLocal


def asset():
    # Match the dimensions/encoding of the reported image using synthetic pixels.
    width, height = 826, 193
    pixels = bytes([220, 235, 245, 255]) * width * height
    bmp = b'BM' + struct.pack('<IHHI', 54 + len(pixels), 0, 0, 54)
    bmp += struct.pack('<IiiHHIIiiII', 40, width, height, 1, 32, 0, len(pixels), 2835, 2835, 0, 0) + pixels
    asset = Path('/app/app/static/tests/freeze-check/target.bmp')
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(bmp)
    return '/static/tests/freeze-check/target.bmp'


def seed():
    image = asset()
    questions = [
        {'question_type': 'hotspot', 'image_url': image, 'options': [
            {'option_text': '(0,0)-(826,0)-(826,193)-(0,193)', 'is_correct': True}]},
        {'question_type': 'matching', 'options': [
            {'option_text': f'Ліворуч {i}', 'matching_text': f'Праворуч {i}'} for i in range(1, 4)]},
        {'question_type': 'multiple_choice', 'options': [
            {'option_text': 'Так', 'is_correct': True}, {'option_text': 'Ще так', 'is_correct': True}, {'option_text': 'Ні'}]},
        {'question_type': 'single_choice', 'options': [{'option_text': 'Так', 'is_correct': True}, {'option_text': 'Ні'}]},
        {'question_type': 'short_text', 'options': [{'option_text': 'Відповідь', 'is_correct': True}]},
        {'question_type': 'sequence', 'options': [{'option_text': f'Крок {i}', 'order_index': i} for i in range(3)]},
        {'question_type': 'image_choice', 'options': [{'option_text': 'Так', 'image_url': image, 'is_correct': True}, {'option_text': 'Ні', 'image_url': image}]},
        {'question_type': 'true_false', 'options': [{'option_text': 'Так', 'is_correct': True}, {'option_text': 'Ні'}]},
    ]
    for i, question in enumerate(questions):
        question.update(question_text=f'Синтетичне питання {i + 1}', order_index=i)
    with SessionLocal() as db:
        owner = crud.create_teacher(db, 'freeze_check', 'Перевірка відмов', 'synthetic-freeze-password')
        test = crud.create_test(db, owner.id, {'title': 'Усі 8 типів питань', 'time_limit_minutes': 45,
            'time_limit_per_question': 60, 'shuffle_questions': False, 'shuffle_options': False,
            'show_result_after_finish': True, 'allow_retake': True, 'questions': questions})
        crud.create_session(db, test.id, 'FREEZE01')
    print('Seeded eight types and a synthetic 826x193 32-bit BMP')


def verify():
    expected = {'Вісім типів': 8, 'Виправлена область': 1, 'Скинута область': 0,
                'Відновлення відхилення': 1, 'Втрачений відгук області': 1, 'Повільне тіло відповіді': 1}
    result = {}
    with SessionLocal() as db:
        for name, count in expected.items():
            attempt = db.query(models.StudentAttempt).filter_by(student_name=name).one()
            submitted = [a for a in attempt.answers if a.answer_text is not None or a.selected_options_json is not None]
            assert attempt.status == models.AttemptStatus.finished, name
            assert len(submitted) == count, (name, len(submitted), count)
            assert all(a.is_correct is not None for a in submitted)
            result[name] = {'status': attempt.status.value, 'confirmed': len(submitted), 'score': attempt.score}
        session = db.query(models.TestSession).filter_by(access_code='FREEZE01').one()
        assert session.is_active
    print(json.dumps(result, ensure_ascii=False, indent=2))


def reset():
    with SessionLocal() as db:
        attempts = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).join(models.Teacher).filter(
            models.Teacher.username == 'freeze_check', ~models.StudentAttempt.student_name.like('Базове відхилення%')).all()
        for attempt in attempts:
            db.delete(attempt)
        db.commit()


{'seed': seed, 'asset': asset, 'verify': verify, 'reset': reset}[sys.argv[1]]()
