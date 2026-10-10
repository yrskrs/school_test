"""Local rosters and authenticated, teacher-scoped school API v2."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import get_db
from app.deps import get_current_teacher
from app.security import _sign, _verify_sign, get_teacher_session
from app.services.roster_backend import operate
from app.templating import templates, _scale_grade
from school_sync.core import SyncError

router = APIRouter()


def csrf(request, teacher):
    return _sign(f"integration:{teacher.id}:{get_teacher_session(request)['t']}")


def check_csrf(request, teacher, data):
    if not hmac.compare_digest(str(data.get('csrf', '')), csrf(request, teacher)):
        raise HTTPException(403, 'Оновіть сторінку та повторіть дію.')


def grant(request, db):
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer ') or not header[7:]:
        raise HTTPException(401, 'Потрібен чинний Bearer-ключ експорту.')
    row = db.query(models.GradeExportGrant).filter_by(token_hash=hashlib.sha256(header[7:].encode()).hexdigest(), active=True).first()
    teacher = db.get(models.Teacher, row.teacher_id) if row else None
    if not teacher or not teacher.is_active:
        raise HTTPException(401, 'Потрібен чинний Bearer-ключ експорту.')
    return teacher


@router.get('/healthz')
def health(db: Session = Depends(get_db)):
    from sqlalchemy import text
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'version': settings.APP_VERSION}


@router.get('/api/v2/roster-sync/')
def roster_api(request: Request, db: Session = Depends(get_db)):
    expected = settings.ROSTER_SYNC_TOKEN
    if not expected or not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + expected):
        raise HTTPException(401, 'Потрібен серверний Bearer-ключ обміну списками.')
    return operate(lambda engine, store: engine.snapshot(), db)


@router.get('/api/v2/journal/roster/')
def grade_roster(request: Request, db: Session = Depends(get_db)):
    teacher = grant(request, db)
    sessions = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id).all()
    class_ids = {s.roster_class_id for s in sessions if s.roster_class_id}
    subject_ids = {s.roster_subject_id for s in sessions if s.roster_subject_id}
    return {'schema_version': 2, 'source': 'schooltest',
        'classes': [{'id': c.id, 'name': c.name} for c in db.query(models.RosterClass).filter(models.RosterClass.id.in_(class_ids))],
        'subjects': [{'id': s.id, 'name': s.name} for s in db.query(models.RosterSubject).filter(models.RosterSubject.id.in_(subject_ids))],
        'students': [{'id': s.id, 'name': s.full_name(), 'class_id': s.class_id} for s in db.query(models.RosterStudent).filter(models.RosterStudent.class_id.in_(class_ids))]}


@router.get('/api/v2/journal/grades/')
def grades(request: Request, db: Session = Depends(get_db)):
    teacher = grant(request, db)
    try:
        class_id = int(request.query_params['class_id'])
        subject_id = int(request.query_params['subject_id'])
        cursor = int(request.query_params.get('cursor', '0'))
        start = dt.date.fromisoformat(request.query_params['date_from'])
        end = dt.date.fromisoformat(request.query_params['date_to'])
        if class_id < 1 or subject_id < 1 or cursor < 0 or start > end:
            raise ValueError
    except (KeyError, ValueError):
        raise HTTPException(400, 'Потрібні ID класу, предмета, період і коректний курсор.')
    qs = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id, models.TestSession.roster_class_id == class_id,
        models.TestSession.roster_subject_id == subject_id)
    if qs.count() > 100000:
        raise HTTPException(422, 'Забагато результатів у цьому контексті.')
    winners = {}
    for attempt in qs.order_by(models.StudentAttempt.id.desc()).yield_per(500):
        identity = (attempt.session_id, attempt.roster_student_id) if attempt.roster_student_id else ('unmapped', attempt.id)
        winners.setdefault(identity, attempt)
    records = []
    for attempt in sorted(winners.values(), key=lambda row: row.id):
        if attempt.id <= cursor or attempt.status not in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
            continue
        if attempt.score is None or attempt.max_score is None or not attempt.finished_at:
            continue
        date = dt.date.fromisoformat(attempt.session.lesson_date) if attempt.session.lesson_date else attempt.finished_at.date()
        if not start <= date <= end:
            continue
        updated = attempt.updated_at or attempt.finished_at
        # Legacy timestamps use the configured original server timezone.
        if updated.tzinfo is None:
            from zoneinfo import ZoneInfo
            updated = updated.replace(tzinfo=ZoneInfo(settings.LEGACY_RESULT_TIMEZONE))
        record = {'source': 'schooltest', 'id': f'schooltest:session:{attempt.session_id}:student:{attempt.roster_student_id}' if attempt.roster_student_id else f'schooltest:unmapped:{attempt.id}',
            'student_id': attempt.roster_student_id, 'class_id': class_id, 'subject_id': subject_id,
            'work_id': attempt.session.test_id, 'title': attempt.session.test.title, 'attempt_id': attempt.id,
            'points': attempt.score, 'max_points': attempt.max_score, 'value': _scale_grade(attempt),
            'grading_scale': attempt.session.test.max_grade or 12, 'date': date.isoformat(),
            'status': 'final', 'updated_at': updated.isoformat(), 'is_ai': False, 'approved_by_teacher': True}
        if attempt.session.lesson_number:
            record['lesson_number'] = attempt.session.lesson_number
        records.append((attempt.id, record))
        if len(records) > 500:
            break
    return {'schema_version': 2, 'source': 'schooltest', 'grades': [row for pk, row in records[:500]],
        'next_cursor': records[499][0] if len(records) > 500 else None}


@router.get('/api/session-roster')
def session_roster(code: str, db: Session = Depends(get_db)):
    session = db.query(models.TestSession).filter_by(access_code=code.strip().upper(), is_active=True).first()
    if not session:
        raise HTTPException(404, 'Активну сесію не знайдено.')
    return {'required': bool(session.roster_class_id), 'students': [
        {'id': s.id, 'name': s.full_name()} for s in db.query(models.RosterStudent).filter_by(class_id=session.roster_class_id, active=True).order_by(models.RosterStudent.last_name)] if session.roster_class_id else []}


@router.api_route('/teacher/roster-sync', methods=['GET', 'POST'])
async def roster_ui(request: Request, db: Session = Depends(get_db), teacher=Depends(get_current_teacher)):
    error, token = '', ''
    if request.method == 'POST':
        data = await request.form()
        check_csrf(request, teacher, data)
        try:
            action = data.get('action')
            def change(engine, store):
                if action == 'share':
                    engine.share('class', int(data['class_id']))
                elif action == 'resolve':
                    expected = _verify_sign(str(data.get('expected', '')))
                    if expected is None:
                        raise SyncError('Недійсна версія форми. Оновіть сторінку.')
                    choice = data.get('choice', 'local')
                    engine.resolve(data['ref'], 'local' if choice == 'local' else int(choice),
                        int(data['native_id']) if data.get('native_id') else None,
                        data.get('create') == '1', json.loads(expected))
                elif action == 'class':
                    name = str(data.get('name', '')).strip()
                    grade = int(data.get('grade', '1'))
                    if not name or len(name) > 50 or not 1 <= grade <= 12:
                        raise SyncError('Потрібні назва класу до 50 символів і номер 1–12.')
                    row = db.get(models.RosterClass, int(data['id'])) if data.get('id') else models.RosterClass()
                    row.name, row.grade_level, row.letter = name, grade, str(data.get('letter', '')).strip()[:10]
                    row.active = data.get('active') == '1'
                    db.add(row)
                    db.flush()
                elif action == 'student':
                    cls = db.get(models.RosterClass, int(data['class_id']))
                    first, last, middle = [str(data.get(k, '')).strip() for k in ('first', 'last', 'middle')]
                    if not cls or not first or not last or any(len(v) > 100 for v in (first, last, middle)):
                        raise SyncError('Оберіть клас, введіть прізвище та ім’я до 100 символів.')
                    row = db.get(models.RosterStudent, int(data['id'])) if data.get('id') else models.RosterStudent()
                    row.class_id, row.first_name, row.last_name, row.middle_name = cls.id, first, last, middle
                    row.active = data.get('active') == '1'
                    db.add(row)
                    db.flush()
                engine.capture()
                store.meta.setdefault('decisions', []).append({'teacher': teacher.id, 'action': action, 'ref': data.get('ref', ''), 'at': dt.datetime.now(dt.timezone.utc).isoformat()})
                store.meta['decisions'] = store.meta['decisions'][-100:]
            operate(change, db)
        except Exception as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, (SyncError, ValueError, KeyError)) else 'Не вдалося зберегти. Перевірте дані та унікальність назви класу.'
    rows, meta = operate(lambda engine, store: (engine.capture() or store.all(), store.meta), db)
    for row in rows:
        row['expected'] = _sign(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return templates.TemplateResponse(request, 'roster_sync.html', {'teacher': teacher, 'rows': rows, 'meta': meta, 'error': error,
        'csrf': csrf(request, teacher), 'classes': db.query(models.RosterClass).order_by(models.RosterClass.name).all(),
        'students': db.query(models.RosterStudent).order_by(models.RosterStudent.class_id, models.RosterStudent.last_name).all()})


@router.api_route('/teacher/integrations', methods=['GET', 'POST'])
async def integration_ui(request: Request, db: Session = Depends(get_db), teacher=Depends(get_current_teacher)):
    error, token = '', ''
    if request.method == 'POST':
        data = await request.form()
        check_csrf(request, teacher, data)
        try:
            action = data.get('action')
            if action in ('key', 'simple_key'):
                # The teacher's own test metadata already names its class and subject.
                # Prepare only missing context after this explicit teacher action.
                for session in db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id):
                    if not session.roster_class_id and session.test.class_name:
                        name = session.test.class_name.strip()
                        group = db.query(models.RosterClass).filter_by(name=name).first()
                        if group is None:
                            number = re.match(r'\d{1,2}', name)
                            grade = int(number.group()) if number and 1 <= int(number.group()) <= 12 else 1
                            group = models.RosterClass(name=name, grade_level=grade)
                            db.add(group)
                            db.flush()
                        session.roster_class_id = group.id
                    if not session.roster_subject_id and session.test.subject:
                        name = session.test.subject.strip()
                        subject = db.query(models.RosterSubject).filter_by(name=name).first()
                        if subject is None:
                            subject = models.RosterSubject(name=name)
                            db.add(subject)
                            db.flush()
                        session.roster_subject_id = subject.id
                if action == 'key':
                    db.query(models.GradeExportGrant).filter_by(teacher_id=teacher.id).update({'active': False})
                token = secrets.token_urlsafe(32)
                db.add(models.GradeExportGrant(teacher_id=teacher.id, token_hash=hashlib.sha256(token.encode()).hexdigest()))
            elif action == 'subject':
                name = str(data.get('name', '')).strip()
                if not name or len(name) > 100:
                    raise SyncError('Введіть назву предмета до 100 символів.')
                db.add(models.RosterSubject(name=name))
            elif action in ('bind', 'attempt'):
                session = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id, models.TestSession.id == int(data['session_id'])).first()
                if not session:
                    raise HTTPException(404, 'Сесію не знайдено.')
                if action == 'bind':
                    class_id, subject_id = int(data['class_id']), int(data['subject_id'])
                    if not db.get(models.RosterClass, class_id) or not db.get(models.RosterSubject, subject_id):
                        raise SyncError('Оберіть локальні клас і предмет.')
                    if session.roster_class_id and (session.roster_class_id != class_id or session.roster_subject_id != subject_id):
                        raise SyncError('Контекст уже прив’язано. Для іншого класу чи предмета створіть нову сесію.')
                    date = dt.date.fromisoformat(data['date'])
                    number = int(data['lesson_number']) if data.get('lesson_number') else None
                    if number is not None and not 1 <= number <= 30:
                        raise SyncError('Номер уроку має бути 1–30.')
                    session.roster_class_id, session.roster_subject_id, session.lesson_date, session.lesson_number = class_id, subject_id, date.isoformat(), number
                else:
                    attempt = db.query(models.StudentAttempt).filter_by(id=int(data['attempt_id']), session_id=session.id).first()
                    pupil = db.get(models.RosterStudent, int(data['student_id']))
                    if not attempt or not pupil or pupil.class_id != session.roster_class_id:
                        raise SyncError('Учень і спроба мають належати вибраній сесії та класу.')
                    if attempt.roster_student_id and attempt.roster_student_id != pupil.id:
                        raise SyncError('Спробу вже пов’язано з іншим ID учня.')
                    attempt.roster_student_id = pupil.id
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, (SyncError, ValueError, KeyError)) else 'Не вдалося зберегти налаштування. Перевірте дані.'
    sessions = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id).order_by(models.TestSession.id.desc()).all()
    site_url = settings.PUBLIC_BASE_URL or str(request.base_url).rstrip('/')
    response = templates.TemplateResponse(request, 'integrations.html', {'teacher': teacher, 'csrf': csrf(request, teacher), 'error': error, 'token': token,
        'site_url': site_url, 'connection_code': json.dumps({'source': 'schooltest', 'site_url': settings.JOURNAL_API_BASE_URL or site_url, 'api_key': token}, ensure_ascii=False) if token else '',
        'classes': db.query(models.RosterClass).all(), 'subjects': db.query(models.RosterSubject).all(),
        'students': db.query(models.RosterStudent).all(), 'sessions': sessions,
        'attempts': db.query(models.StudentAttempt).filter(models.StudentAttempt.session_id.in_([s.id for s in sessions]), models.StudentAttempt.roster_student_id.is_(None)).all()})
    response.headers['Cache-Control'] = 'no-store'
    return response
