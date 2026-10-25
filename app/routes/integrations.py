"""Local rosters and authenticated, teacher-scoped school API v2."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import get_db
from app.deps import get_current_teacher
from app.security import _sign, _verify_sign, get_teacher_session
from app.services.roster_backend import operate
from app.services.roster_review import prepare_review
from app.services.roster_service import teacher_roster_classes
from app.templating import templates, _scale_grade
from school_sync.core import SyncError

router = APIRouter()


def local_instance_id(db):
    state = db.get(models.RosterState, 1)
    identity = state.data.get('origin') if state and isinstance(state.data, dict) else None
    return identity or operate(lambda engine, store: engine.origin, db)


@router.get('/api/v2/ping')
@router.get('/api/v2/ping/', include_in_schema=False)
def service_ping(db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse
    from school_sync.discovery import ping_payload
    return JSONResponse(ping_payload('schooltest', local_instance_id(db), settings.APP_VERSION),
        headers={'Cache-Control': 'no-store'})


@router.get('/api/v2/journal/connection/')
def journal_connection_info(request: Request, db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse
    from school_sync.discovery import ping_payload
    grant(request, db)
    data = ping_payload('schooltest', local_instance_id(db), settings.APP_VERSION)
    data.update(schema_version=2, permissions={'export': True, 'roster_import': True})
    return JSONResponse(data, headers={'Cache-Control': 'no-store'})


def csrf(request, teacher):
    return _sign(f"integration:{teacher.id}:{get_teacher_session(request)['t']}")


def check_csrf(request, teacher, data):
    if not hmac.compare_digest(str(data.get('csrf', '')), csrf(request, teacher)):
        raise HTTPException(403, 'Оновіть сторінку та повторіть дію.')


def grant(request, db):
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer ') or not header[7:]:
        raise HTTPException(401, 'Потрібен чинний Bearer-ключ експорту.')
    token_str = header[7:].strip()
    token_hash = hashlib.sha256(token_str.encode()).hexdigest()
    row = db.query(models.GradeExportGrant).filter_by(token_hash=token_hash).first()
    if not row:
        raise HTTPException(401, 'Потрібен чинний Bearer-ключ експорту.')
    if not row.active:
        def log_blocked(engine, store):
            item = store.meta.setdefault('journal_api_stats', {}).setdefault(str(row.id), {'requests': 0})
            item['blocked_attempts'] = item.get('blocked_attempts', 0) + 1
            item['last_blocked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            client_ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '127.0.0.1')
            item['last_blocked_ip'] = client_ip
        operate(log_blocked, db)
        raise HTTPException(401, 'Ключ API тимчасово поставлено на паузу. Активуйте його в налаштуваннях тестів.')

    teacher = db.get(models.Teacher, row.teacher_id) if row else None
    if not teacher or not teacher.is_active:
        raise HTTPException(401, 'Потрібен чинний Bearer-ключ експорту.')

    def usage(engine, store):
        item = store.meta.setdefault('journal_api_stats', {}).setdefault(str(row.id), {'requests': 0})
        item['requests'] = item.get('requests', 0) + 1
        now_dt = dt.datetime.now(dt.timezone.utc)
        item['last_used'] = now_dt.isoformat()
        path = request.url.path
        item['path'] = path
        item['method'] = request.method
        client_ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '127.0.0.1')
        item['client_ip'] = client_ip

        # Endpoint categorization
        endpoints = item.setdefault('endpoints', {})
        if '/journal/roster/' in path:
            ep_name = 'Синхронізація списків (POST)' if request.method == 'POST' else 'Запит списків (GET)'
        elif '/journal/grades/' in path:
            ep_name = 'Експорт оцінок'
        else:
            ep_name = f"{request.method} {path}"
        endpoints[ep_name] = endpoints.get(ep_name, 0) + 1

        # Recent activity log (keep last 10 entries)
        recent = item.setdefault('recent_calls', [])
        recent.insert(0, {
            'time': now_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'action': ep_name,
            'method': request.method,
            'path': path,
            'client_ip': client_ip,
            'status': '200 OK'
        })
        item['recent_calls'] = recent[:10]

    operate(usage, db)
    return teacher


@router.api_route('/healthz', methods=['GET', 'HEAD'])
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
    # Prepared metadata exposes names without turning legacy name login into
    # mandatory roster login. Only the teacher's explicit bind changes that.
    class_names = {s.test.class_name.strip() for s in sessions if not s.roster_class_id and s.test.class_name}
    subject_names = {s.test.subject.strip() for s in sessions if not s.roster_subject_id and s.test.subject}
    class_ids.update(row.id for row in db.query(models.RosterClass).filter(models.RosterClass.name.in_(class_names)))
    from school_sync.journal_roster import imported_classes
    class_ids.update(operate(lambda engine, store: imported_classes(store, teacher.id), db))
    class_ids -= {row.id for row in db.query(models.RosterClass).filter_by(letter='ПК')
                  if row.name.endswith(' (Позакласні)')}
    subject_ids.update(row.id for row in db.query(models.RosterSubject).filter(models.RosterSubject.name.in_(subject_names)))
    subject_ids.update(operate(lambda engine, store: set(store.meta.get('journal_subjects', {}).get(str(teacher.id), [])), db))
    return {'schema_version': 2, 'source': 'schooltest', 'capabilities': ['journal_roster_import'], 'instance_id': operate(lambda engine, store: engine.origin, db),
        'classes': [{'id': c.id, 'name': c.name} for c in db.query(models.RosterClass).filter(models.RosterClass.id.in_(class_ids))],
        'subjects': [{'id': s.id, 'name': s.name} for s in db.query(models.RosterSubject).filter(models.RosterSubject.id.in_(subject_ids))],
        'students': [{'id': s.id, 'name': s.full_name(), 'class_id': s.class_id} for s in db.query(models.RosterStudent).filter(models.RosterStudent.class_id.in_(class_ids))]}


@router.post('/api/v2/journal/roster/')
async def receive_journal_roster(request: Request, db: Session = Depends(get_db)):
    teacher = grant(request, db)
    from school_sync.journal_roster import import_students, imported_classes
    try:
        body = await request.body()
        if len(body) > 2 * 1024 * 1024:
            raise SyncError('Список перевищує 2 МБ.')
        payload = json.loads(body)
        def receive(engine, store):
            own = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id).all()
            names = {s.test.class_name.strip() for s in own if s.test.class_name}
            allowed = {s.roster_class_id for s in own if s.roster_class_id}
            allowed.update(c.id for c in db.query(models.RosterClass).filter(models.RosterClass.name.in_(names)))
            allowed.update(imported_classes(store, teacher.id))
            result = import_students(engine, store, payload, teacher.id, allowed)
            name = payload.get('subject', {}).get('name', '').strip()
            if not name or len(name) > 100:
                raise SyncError('Потрібна назва предмета до 100 символів.')
            subject = db.query(models.RosterSubject).filter_by(name=name).first()
            if subject is None:
                subject = models.RosterSubject(name=name)
                db.add(subject); db.flush()
            result.update(source='schooltest', instance_id=engine.origin, subject_id=subject.id)
            ids = store.meta.setdefault('journal_subjects', {}).setdefault(str(teacher.id), [])
            if subject.id not in ids:
                ids.append(subject.id)
            return result
        return operate(receive, db)
    except (SyncError, ValueError, TypeError, KeyError, AttributeError) as error:
        db.rollback()
        raise HTTPException(400, str(error) if isinstance(error, SyncError) else 'Некоректний список. Місцеві дані збережено.')


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
    group, subject = db.get(models.RosterClass, class_id), db.get(models.RosterSubject, subject_id)
    if not group or not subject:
        return {'schema_version': 2, 'source': 'schooltest', 'grades': [], 'next_cursor': None}
    from school_sync.journal_roster import historical_class_names
    names = operate(lambda engine, store: historical_class_names(store, teacher.id, class_id, group.name), db)
    qs = db.query(models.StudentAttempt).join(models.TestSession).join(models.Test).filter(
        models.Test.teacher_id == teacher.id,
        or_(models.TestSession.roster_class_id == class_id,
            and_(models.TestSession.roster_class_id.is_(None), func.trim(models.Test.class_name).in_(names))),
        or_(models.TestSession.roster_subject_id == subject_id,
            and_(models.TestSession.roster_subject_id.is_(None), func.trim(models.Test.subject) == subject.name)))
    if qs.count() > 100000:
        raise HTTPException(422, 'Забагато результатів у цьому контексті.')
    winners = {}
    for attempt in qs.order_by(models.StudentAttempt.id.desc()).yield_per(500):
        identity = (attempt.session_id, attempt.roster_student_id or attempt.outside_student_id) if attempt.roster_student_id or attempt.outside_student_id else ('unmapped', attempt.id)
        winners.setdefault(identity, attempt)
    records = []
    for attempt in sorted(winners.values(), key=lambda row: row.id):
        if attempt.id <= cursor or attempt.status not in (models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped):
            continue
        if attempt.score is None or attempt.max_score is None or not attempt.finished_at:
            continue
        value = _scale_grade(attempt)
        # Keep the original test result, including zero, on the source. A zero
        # or another grading scale is not a final 1–12 journal mark.
        if (attempt.session.test.max_grade or 12) != 12 or not str(value).isdigit() or not 1 <= int(value) <= 12:
            continue
        date = dt.date.fromisoformat(attempt.session.lesson_date) if attempt.session.lesson_date else attempt.finished_at.date()
        if not start <= date <= end:
            continue
        updated = attempt.updated_at or attempt.finished_at
        # Legacy timestamps use the configured original server timezone.
        if updated.tzinfo is None:
            from zoneinfo import ZoneInfo
            updated = updated.replace(tzinfo=ZoneInfo(settings.LEGACY_RESULT_TIMEZONE))
        record = {'source': 'schooltest', 'id': f'schooltest:session:{attempt.session_id}:student:{attempt.roster_student_id}' if attempt.roster_student_id else f'schooltest:session:{attempt.session_id}:outside:{attempt.outside_student_id}' if attempt.outside_student_id else f'schooltest:unmapped:{attempt.id}',
            'student_id': attempt.roster_student_id, 'student_name': attempt.student_name,
            'student_key': str(attempt.roster_student_id) if attempt.roster_student_id else f'outside:{attempt.outside_student_id}' if attempt.outside_student_id else f'unmapped:{attempt.id}',
            'outside_student_id': attempt.outside_student_id if not attempt.roster_student_id else None,
            'class_id': class_id, 'subject_id': subject_id,
            'work_id': attempt.session.test_id, 'title': attempt.session.test.title, 'attempt_id': attempt.id,
            'points': attempt.score, 'max_points': attempt.max_score, 'value': value,
            'grading_scale': attempt.session.test.max_grade or 12, 'date': date.isoformat(),
            'status': 'final', 'updated_at': updated.isoformat(), 'is_ai': False, 'approved_by_teacher': True}
        if attempt.session.lesson_number:
            record['lesson_number'] = attempt.session.lesson_number
        records.append((attempt.id, record))
        if len(records) > 500:
            break
    return {'schema_version': 2, 'source': 'schooltest', 'grades': [row for pk, row in records[:500]],
        'next_cursor': records[499][0] if len(records) > 500 else None}


def get_session_roster_data(db: Session, session: models.TestSession):
    from app.services.roster_service import resolve_roster_class
    from app.services.test_service import question_counts
    group, error = resolve_roster_class(db, session.test, session.roster_class_id)
    group_id = group.id if group else None
    return {
        'required': bool(session.roster_class_id or error),
        'allow_outside_class': bool(session.test and session.test.allow_outside_class),
        'available': bool(group_id),
        'class_id': group_id,
        'error': error,
        'title': session.test.title if session.test else '',
        'subject': session.display_subject_name or '',
        'class_name': session.display_class_name or '',
        'time_limit': session.test.time_limit_minutes if session.test else None,
        **question_counts(db, session.test),
        'students': [
            {'id': s.id, 'name': s.full_name()}
            for s in db.query(models.RosterStudent).filter_by(class_id=group_id, active=True).order_by(models.RosterStudent.last_name, models.RosterStudent.first_name)
        ] if group_id and not error else []
    }


@router.get('/api/session-roster')
def session_roster(code: str, db: Session = Depends(get_db)):
    session = db.query(models.TestSession).filter_by(access_code=code.strip().upper(), is_active=True).first()
    if not session:
        raise HTTPException(404, 'Активну сесію не знайдено.')
    return get_session_roster_data(db, session)


@router.api_route('/teacher/roster-sync', methods=['GET', 'POST'])
async def roster_ui(request: Request, db: Session = Depends(get_db), teacher=Depends(get_current_teacher)):
    error, token = '', ''
    last_action = ''
    last_class_id = None
    success_msg = ''
    if request.method == 'POST':
        data = await request.form()
        check_csrf(request, teacher, data)
        try:
            action = data.get('action')
            last_action = action or ''
            if action == 'student' and data.get('class_id'):
                try:
                    last_class_id = int(data.get('class_id'))
                except (ValueError, TypeError):
                    pass
            elif action == 'class' and data.get('id'):
                try:
                    last_class_id = int(data.get('id'))
                except (ValueError, TypeError):
                    pass
            def change(engine, store):
                nonlocal last_class_id
                engine.capture()
                allowed = teacher_roster_classes(db, teacher.id, active_only=False)
                allowed_ids = {group.id for group in allowed}
                def check_record(record):
                    if not record or record['kind'] == 'alias':
                        raise SyncError('Запис недоступний для рішення.')
                    parent = record if record['kind'] == 'class' else store.get(engine.canonical(record['payload']['class_ref']))
                    if parent and parent.get('native_id') is not None and parent['native_id'] not in allowed_ids:
                        raise SyncError('Клас недоступний цьому вчителю.')
                if action == 'share':
                    if int(data['class_id']) not in allowed_ids:
                        raise SyncError('Клас недоступний цьому вчителю.')
                    engine.share('class', int(data['class_id']))
                    last_class_id = int(data['class_id'])
                elif action == 'resolve':
                    expected = _verify_sign(str(data.get('expected', '')))
                    if expected is None:
                        raise SyncError('Недійсна версія форми. Оновіть сторінку.')
                    check_record(store.get(data['ref']))
                    if data.get('native_id'):
                        kind = store.get(data['ref'])['kind']
                        target = engine.native.read(kind, int(data['native_id']))
                        target_class = int(data['native_id']) if kind == 'class' else target.get('native_class_id') if target else None
                        if target_class not in allowed_ids:
                            raise SyncError('Вибраний запис недоступний цьому вчителю.')
                        from school_sync.journal_roster import signature
                        versions = _verify_sign(str(data.get('native_versions', '')))
                        if versions is None:
                            raise SyncError('Недійсна версія списку відповідностей. Оновіть сторінку.')
                        if json.loads(versions).get(str(data['native_id'])) != signature(target):
                            raise SyncError('Вибраний запис змінився після перегляду. Оновіть сторінку.')
                    choice = data.get('choice', 'local')
                    engine.resolve(data['ref'], 'local' if choice == 'local' else int(choice),
                        int(data['native_id']) if data.get('native_id') else None,
                        data.get('create') == '1', json.loads(expected))
                elif action == 'resolve_matches':
                    signed = _verify_sign(str(data.get('matches', '')))
                    if signed is None:
                        raise SyncError('Недійсна версія підбору. Оновіть сторінку.')
                    matches = json.loads(signed)
                    if not isinstance(matches, list) or not matches or len(matches) > 5000:
                        raise SyncError('Немає доступних відповідностей для підтвердження.')
                    pupils = db.query(models.RosterStudent).filter(models.RosterStudent.class_id.in_(allowed_ids)).all()
                    _, current_batches = prepare_review(engine, store.all(), allowed, pupils)
                    eligible = {(match['ref'], match['native_id']) for batch in current_batches.values() for match in batch}
                    for match in matches:
                        record = store.get(match['ref'])
                        check_record(record)
                        if ((match['ref'], match['native_id']) not in eligible or record != match['expected']
                                or engine.payload('student', match['native_id']) != match['native_expected']):
                            raise SyncError('Список змінився після підбору. Оновіть сторінку й перевірте відповідності.')
                    for match in matches:
                        engine.resolve(match['ref'], 'local', match['native_id'], expected=match['expected'])
                elif action == 'class':
                    name = str(data.get('name', '')).strip()
                    from app.services.class_labels import class_fields
                    inferred = class_fields(name)
                    grade = inferred['grade_level'] if inferred else int(data.get('grade', '1'))
                    if not name or len(name) > 50 or not 1 <= grade <= 12:
                        raise SyncError('Потрібні назва класу до 50 символів і номер 1–12.')
                    if data.get('id') and int(data['id']) not in allowed_ids:
                        raise SyncError('Клас недоступний цьому вчителю.')
                    row = db.get(models.RosterClass, int(data['id'])) if data.get('id') else models.RosterClass()
                    row.name, row.grade_level, row.letter = name, grade, inferred['letter'] if inferred else str(data.get('letter', '')).strip()[:10]
                    row.active = data.get('active') == '1'
                    db.add(row)
                    db.flush()
                    last_class_id = row.id
                elif action == 'student':
                    cls = db.get(models.RosterClass, int(data['class_id']))
                    first, last, middle = [str(data.get(k, '')).strip() for k in ('first', 'last', 'middle')]
                    if not cls or cls.id not in allowed_ids or not first or not last or any(len(v) > 100 for v in (first, last, middle)):
                        raise SyncError('Оберіть клас, введіть прізвище та ім’я до 100 символів.')
                    row = db.get(models.RosterStudent, int(data['id'])) if data.get('id') else models.RosterStudent()
                    if row is None or (row.id is not None and row.class_id is not None and row.class_id not in allowed_ids):
                        raise SyncError('Учень недоступний цьому вчителю.')
                    row.class_id, row.first_name, row.last_name, row.middle_name = cls.id, first, last, middle
                    row.active = data.get('active') == '1'
                    db.add(row)
                    db.flush()
                    last_class_id = cls.id
                engine.capture()
                store.meta.setdefault('decisions', []).append({'teacher': teacher.id, 'action': action, 'ref': data.get('ref', ''), 'at': dt.datetime.now(dt.timezone.utc).isoformat()})
                store.meta['decisions'] = store.meta['decisions'][-100:]
            operate(change, db)
            if action == 'student':
                success_msg = 'Дані учня успішно збережено.'
            elif action == 'class':
                success_msg = 'Дані класу успішно збережено.'
            elif action == 'share':
                success_msg = 'Клас приєднано до обміну.'
            elif action == 'resolve':
                success_msg = 'Рішення синхронізації успішно застосовано.'
            elif action == 'resolve_matches':
                success_msg = 'Знайдені відповідності підтверджено. Подальші зміни передаватимуться за сталими ID.'
        except Exception as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, (SyncError, ValueError, KeyError)) else 'Не вдалося зберегти. Перевірте дані та унікальність назви класу.'
    def review(engine, store):
        engine.capture()
        all_classes = teacher_roster_classes(db, teacher.id, active_only=False)
        all_students = db.query(models.RosterStudent).filter(or_(models.RosterStudent.class_id.in_([c.id for c in all_classes]),
            models.RosterStudent.class_id.is_(None))).order_by(models.RosterStudent.class_id, models.RosterStudent.last_name, models.RosterStudent.first_name).all()
        reviews, batches = prepare_review(engine, store.all(), all_classes, all_students)
        return reviews, batches, store.meta, all_classes, all_students
    reviews, batches, meta, all_classes, all_students = operate(review, db)
    review_groups = {}
    for item in reviews:
        record = item['record']
        # Sign the engine record before adding presentation-only fields.
        record['expected'] = _sign(json.dumps(record, sort_keys=True, ensure_ascii=False))
        item['native_versions'] = _sign(json.dumps(item['native_versions'], sort_keys=True))
        if record['status'] != 'ready':
            key = item['class_id'] if record['kind'] == 'student' else 'classes'
            name = 'Класи' if key == 'classes' else item['class_name'] or 'Спочатку прив’яжіть клас'
            group = review_groups.setdefault(key, {'name': name, 'items': []})
            group['items'].append(item)
    for key, matches in batches.items():
        review_groups[key]['matches'] = _sign(json.dumps(matches, sort_keys=True, ensure_ascii=False))
        review_groups[key]['match_count'] = len(matches)
    review_groups = dict(sorted(review_groups.items(), key=lambda pair: (pair[0] != 'classes', pair[1]['name'])))
    rows = [item['record'] for item in reviews]

    students_by_class = {c.id: [] for c in all_classes}
    unassigned_students = []
    for s in all_students:
        if s.class_id in students_by_class:
            students_by_class[s.class_id].append(s)
        else:
            unassigned_students.append(s)

    unresolved_count = sum(1 for r in rows if r.get('status') != 'ready')
    from app.services.outside_students import outside_summary

    return templates.TemplateResponse(request, 'roster_sync.html', {
        'teacher': teacher,
        'rows': rows,
        'meta': meta,
        'error': error,
        'success_msg': success_msg,
        'csrf': csrf(request, teacher),
        'classes': all_classes,
        'students': all_students,
        'students_by_class': students_by_class,
        'unassigned_students': unassigned_students,
        'unresolved_count': unresolved_count,
        'review_groups': review_groups,
        'outside_summary': outside_summary(db, teacher.id),
        'last_action': last_action,
        'last_class_id': last_class_id,
    })


@router.api_route('/teacher/integrations', methods=['GET', 'POST'])
async def integration_ui(request: Request, db: Session = Depends(get_db), teacher=Depends(get_current_teacher)):
    error, token, success_msg = '', '', ''
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
                    if not session.roster_subject_id and session.test.subject:
                        name = session.test.subject.strip()
                        subject = db.query(models.RosterSubject).filter_by(name=name).first()
                        if subject is None:
                            subject = models.RosterSubject(name=name)
                            db.add(subject)
                            db.flush()
                if action == 'key':
                    db.query(models.GradeExportGrant).filter_by(teacher_id=teacher.id).update({'active': False})
                token = secrets.token_urlsafe(32)
                grant_row = models.GradeExportGrant(teacher_id=teacher.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), active=True)
                db.add(grant_row)
                db.flush()
                raw_name = str(data.get('name', '')).strip()
                raw_desc = str(data.get('description', '')).strip()
                if len(raw_name) > 100:
                    raise SyncError('Назва ключа має бути до 100 символів.')
                if len(raw_desc) > 500:
                    raise SyncError('Опис ключа має бути до 500 символів.')
                grant_name = raw_name if raw_name else f"Ключ API #{grant_row.id}"
                def record_grant_meta(engine, store):
                    store.meta.setdefault('api_grants_meta', {})[str(grant_row.id)] = {
                        'name': grant_name,
                        'description': raw_desc,
                        'created_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M'),
                        'updated_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M'),
                    }
                operate(record_grant_meta, db)
                success_msg = f'Ключ API «{grant_name}» успішно створено.'
            elif action == 'edit_key':
                grant_id = int(data.get('grant_id', 0))
                grant_row = db.query(models.GradeExportGrant).filter_by(id=grant_id, teacher_id=teacher.id).first()
                if not grant_row:
                    raise HTTPException(404, 'Ключ API не знайдено.')
                new_name = str(data.get('name', '')).strip()
                new_desc = str(data.get('description', '')).strip()
                if len(new_name) > 100:
                    raise SyncError('Назва ключа має бути до 100 символів.')
                if len(new_desc) > 500:
                    raise SyncError('Опис ключа має бути до 500 символів.')
                final_name = new_name if new_name else f"Ключ API #{grant_id}"

                rotated = False
                rotate_val = str(data.get('rotate_token', '')).strip().lower()
                custom_tok = str(data.get('custom_token', '')).strip()
                if rotate_val in ('1', 'true', 'yes', 'on'):
                    token = secrets.token_urlsafe(32)
                    grant_row.token_hash = hashlib.sha256(token.encode()).hexdigest()
                    rotated = True
                elif custom_tok:
                    if len(custom_tok) < 16 or any(c in custom_tok for c in ('\r', '\n', ' ')):
                        raise SyncError('Ключ API повинен містити щонайменше 16 символів без пробілів і переносів рядків.')
                    new_hash = hashlib.sha256(custom_tok.encode()).hexdigest()
                    existing = db.query(models.GradeExportGrant).filter_by(token_hash=new_hash).first()
                    if existing and existing.id != grant_id:
                        raise SyncError('Такий ключ API вже використовується іншим підключенням.')
                    grant_row.token_hash = new_hash
                    token = custom_tok
                    rotated = True

                def update_grant_meta(engine, store):
                    entry = store.meta.setdefault('api_grants_meta', {}).setdefault(str(grant_id), {})
                    entry['name'] = final_name
                    entry['description'] = new_desc
                    entry['updated_at'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
                operate(update_grant_meta, db)
                if rotated:
                    success_msg = f'Ключ «{final_name}» оновлено. Згенеровано новий секретний ключ доступу — скопіюйте його нижче.'
                else:
                    success_msg = f'Дані ключа «{final_name}» успішно збережено.'
            elif action == 'toggle_key':
                grant_id = int(data.get('grant_id', 0))
                grant_row = db.query(models.GradeExportGrant).filter_by(id=grant_id, teacher_id=teacher.id).first()
                if not grant_row:
                    raise HTTPException(404, 'Ключ API не знайдено.')
                grant_row.active = not grant_row.active
                db.flush()
                key_name = operate(lambda engine, store: store.meta.get('api_grants_meta', {}).get(str(grant_id), {}).get('name'), db) or f"Ключ API #{grant_id}"
                status_word = 'активовано' if grant_row.active else 'поставлено на паузу'
                success_msg = f'Ключ «{key_name}» успішно {status_word}.'
            elif action == 'rename_key':
                grant_id = int(data.get('grant_id', 0))
                new_name = str(data.get('name', '')).strip()
                new_desc = str(data.get('description', '')).strip() if 'description' in data else None
                if not new_name or len(new_name) > 100:
                    raise SyncError('Введіть назву ключа (до 100 символів).')
                if new_desc is not None and len(new_desc) > 500:
                    raise SyncError('Опис ключа має бути до 500 символів.')
                grant_row = db.query(models.GradeExportGrant).filter_by(id=grant_id, teacher_id=teacher.id).first()
                if not grant_row:
                    raise HTTPException(404, 'Ключ API не знайдено.')
                def rename_grant(engine, store):
                    entry = store.meta.setdefault('api_grants_meta', {}).setdefault(str(grant_id), {})
                    entry['name'] = new_name
                    if new_desc is not None:
                        entry['description'] = new_desc
                    entry['updated_at'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
                operate(rename_grant, db)
                success_msg = f'Назву ключа оновлено на «{new_name}».'
            elif action == 'reset_stats':
                grant_id = int(data.get('grant_id', 0))
                grant_row = db.query(models.GradeExportGrant).filter_by(id=grant_id, teacher_id=teacher.id).first()
                if not grant_row:
                    raise HTTPException(404, 'Ключ API не знайдено.')
                key_name = operate(lambda engine, store: store.meta.get('api_grants_meta', {}).get(str(grant_id), {}).get('name'), db) or f"Ключ API #{grant_id}"
                def reset_grant_stats(engine, store):
                    if str(grant_id) in store.meta.get('journal_api_stats', {}):
                        store.meta['journal_api_stats'][str(grant_id)] = {
                            'requests': 0,
                            'endpoints': {},
                            'recent_calls': [],
                            'last_used': '',
                            'reset_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
                        }
                operate(reset_grant_stats, db)
                success_msg = f'Статистику запитів для ключа «{key_name}» успішно скинуто.'
            elif action == 'delete_key':
                grant_id = int(data.get('grant_id', 0))
                grant_row = db.query(models.GradeExportGrant).filter_by(id=grant_id, teacher_id=teacher.id).first()
                if not grant_row:
                    raise HTTPException(404, 'Ключ API не знайдено.')
                key_name = operate(lambda engine, store: store.meta.get('api_grants_meta', {}).get(str(grant_id), {}).get('name'), db) or f"Ключ API #{grant_id}"
                db.delete(grant_row)
                def remove_grant_meta(engine, store):
                    store.meta.get('api_grants_meta', {}).pop(str(grant_id), None)
                    store.meta.get('journal_api_stats', {}).pop(str(grant_id), None)
                operate(remove_grant_meta, db)
                success_msg = f'Ключ «{key_name}» успішно видалено.'
            elif action == 'subject':
                name = str(data.get('name', '')).strip()
                if not name or len(name) > 100:
                    raise SyncError('Введіть назву предмета до 100 символів.')
                db.add(models.RosterSubject(name=name))
                success_msg = f'Предмет «{name}» успішно додано.'
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
                    success_msg = 'Контекст сесії успішно збережено.'
                else:
                    attempt = db.query(models.StudentAttempt).filter_by(id=int(data['attempt_id']), session_id=session.id).first()
                    pupil = db.get(models.RosterStudent, int(data['student_id']))
                    if not attempt or not pupil or pupil.class_id != session.roster_class_id:
                        raise SyncError('Учень і спроба мають належати вибраній сесії та класу.')
                    if attempt.roster_student_id and attempt.roster_student_id != pupil.id:
                        raise SyncError('Спробу вже пов’язано з іншим ID учня.')
                    attempt.roster_student_id = pupil.id
                    success_msg = 'Спробу успішно зв’язано з учнем.'
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            error = str(exc) if isinstance(exc, (SyncError, ValueError, KeyError)) else 'Не вдалося зберегти налаштування. Перевірте дані.'

    teacher_grants = db.query(models.GradeExportGrant).filter_by(teacher_id=teacher.id).order_by(models.GradeExportGrant.id.desc()).all()
    grant_ids_set = {r.id for r in teacher_grants}

    meta_and_stats = operate(lambda engine, store: (
        {k: v for k, v in store.meta.get('api_grants_meta', {}).items() if int(k) in grant_ids_set},
        {k: v for k, v in store.meta.get('journal_api_stats', {}).items() if int(k) in grant_ids_set}
    ), db)
    api_grants_meta, filtered_api_stats = meta_and_stats

    grants_list = []
    total_requests_count = 0
    for g in teacher_grants:
        g_meta = api_grants_meta.get(str(g.id), {})
        g_stats = filtered_api_stats.get(str(g.id), {})
        reqs = g_stats.get('requests', 0)
        total_requests_count += reqs
        grants_list.append({
            'id': g.id,
            'active': bool(g.active),
            'name': g_meta.get('name') or f"Ключ API #{g.id}",
            'description': g_meta.get('description') or '',
            'created_at': g_meta.get('created_at', ''),
            'updated_at': g_meta.get('updated_at', ''),
            'requests': reqs,
            'last_used': g_stats.get('last_used', ''),
            'path': g_stats.get('path', ''),
            'method': g_stats.get('method', ''),
            'client_ip': g_stats.get('client_ip', ''),
            'endpoints': g_stats.get('endpoints', {}),
            'recent_calls': g_stats.get('recent_calls', []),
            'blocked_attempts': g_stats.get('blocked_attempts', 0),
            'last_blocked_at': g_stats.get('last_blocked_at', ''),
            'last_blocked_ip': g_stats.get('last_blocked_ip', ''),
        })

    sessions = db.query(models.TestSession).join(models.Test).filter(models.Test.teacher_id == teacher.id).order_by(models.TestSession.id.desc()).all()
    site_url = str(request.base_url).rstrip('/')
    from school_sync.network_inventory import connection_addresses
    response = templates.TemplateResponse(request, 'integrations.html', {
        'teacher': teacher,
        'csrf': csrf(request, teacher),
        'error': error,
        'success_msg': success_msg,
        'token': token,
        'site_url': site_url,
        'source_instance_id': local_instance_id(db),
        'connection_addresses': connection_addresses(site_url, os.getenv('APP_PORT') or '8080'),
        'connection_code': json.dumps({'source': 'schooltest', 'site_url': site_url, 'api_key': token}, ensure_ascii=False) if token else '',
        'classes': db.query(models.RosterClass).all(),
        'subjects': db.query(models.RosterSubject).all(),
        'students': db.query(models.RosterStudent).all(),
        'sessions': sessions,
        'api_stats': filtered_api_stats,
        'api_grants': grants_list,
        'total_grants_count': len(grants_list),
        'active_grants_count': sum(1 for g in grants_list if g['active']),
        'paused_grants_count': sum(1 for g in grants_list if not g['active']),
        'total_requests_count': total_requests_count,
        'attempts': db.query(models.StudentAttempt).filter(
            models.StudentAttempt.session_id.in_([s.id for s in sessions]),
            models.StudentAttempt.roster_student_id.is_(None)
        ).all()
    })
    response.headers['Cache-Control'] = 'no-store'
    return response
