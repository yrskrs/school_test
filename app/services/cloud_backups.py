"""SchoolTest graph adapter: only fresh-target restores, no destructive merging."""
import contextlib
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, select, text

from app import models
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.deps import can_manage_updates
from cloud_backup.archive import json_bytes, safe_path
from cloud_backup.files import bytes_source, destination, file_source, install_files
from cloud_backup.state import State

EXCLUDE = {'grade_export_grants'}  # reconnect API grants after recovery


def state():
    return State(os.environ.get('CLOUD_BACKUP_ROOT', str(settings.BASE_DIR/'data'/'cloud-backup')), settings.SECRET_KEY)


def schema():
    return {t.name: [c.name for c in t.columns] for t in Base.metadata.sorted_tables if t.name not in EXCLUDE}


class Adapter:
    app = 'schooltest'

    @staticmethod
    def owner(teacher):
        identity = teacher.created_at.isoformat() if teacher.created_at else (teacher.auth_nonce or teacher.hashed_password)
        fingerprint = hashlib.sha256(f'{teacher.username}:{identity}'.encode()).hexdigest()[:32]
        return f'{teacher.id}~{fingerprint}'

    def resolve(self, owner):
        number = int(str(owner).split('~')[0])
        if '~' in str(owner):
            with SessionLocal() as db:
                teacher = db.get(models.Teacher, number)
                if not teacher or self.owner(teacher) != owner:
                    raise ValueError('Обліковий запис змінився. Налаштуйте резервування повторно')
        return number

    def authorize(self, owner, scope):
        owner = self.resolve(owner)
        if scope not in ('personal', 'server'):
            raise ValueError('Некоректний обсяг копії')
        with SessionLocal() as db:
            teacher = db.get(models.Teacher, int(owner))
            if not teacher or not teacher.is_active or (scope == 'server' and not can_manage_updates(db, teacher)):
                raise ValueError('Недостатньо прав для цієї операції')

    @contextlib.contextmanager
    def snapshot(self, owner, config):
        self.authorize(owner, config['scope'])
        owner = self.resolve(owner)
        with engine.connect() as conn:
            if engine.dialect.name == 'postgresql':
                conn = conn.execution_options(isolation_level='REPEATABLE READ')
            with conn.begin():
                rows = {t.name: [dict(r) for r in conn.execute(select(t)).mappings()] for t in Base.metadata.sorted_tables if t.name not in EXCLUDE}
        personal = config['scope'] == 'personal'
        if personal:
            rows['teachers'] = [r for r in rows['teachers'] if r['id'] == int(owner)]
            rows['tests'] = [r for r in rows['tests'] if r['teacher_id'] == int(owner)]
            tests = {r['id'] for r in rows['tests']}
            rows['questions'] = [r for r in rows['questions'] if r['test_id'] in tests]
            questions = {r['id'] for r in rows['questions']}
            rows['answer_options'] = [r for r in rows['answer_options'] if r['question_id'] in questions]
            rows['test_sessions'] = [r for r in rows['test_sessions'] if r['test_id'] in tests] if config['students'] else []
            sessions = {r['id'] for r in rows['test_sessions']}
            rows['student_attempts'] = [r for r in rows['student_attempts'] if r['session_id'] in sessions]
            attempts = {r['id'] for r in rows['student_attempts']}
            for name in ('student_answers', 'event_logs'):
                rows[name] = [r for r in rows[name] if r['attempt_id'] in attempts]
            rows['teacher_logs'] = []
            class_names = {r['class_name'] for r in rows['tests']}
            class_ids = {r['roster_class_id'] for r in rows['test_sessions']}
            class_names.update(x.strip() for x in (rows['teachers'][0].get('classes') or '').split(','))
            normal_classes = {r['id'] for r in rows['roster_classes'] if r['id'] in class_ids or r['name'] in class_names}
            referenced_students = {r['roster_student_id'] for r in rows['student_attempts']}
            class_ids |= {r['class_id'] for r in rows['roster_students'] if r['id'] in referenced_students}
            rows['roster_classes'] = [r for r in rows['roster_classes'] if r['id'] in class_ids or r['id'] in normal_classes]
            rows['roster_students'] = [r for r in rows['roster_students'] if r['class_id'] in normal_classes or r['id'] in referenced_students]
            subject_names = {r['subject'] for r in rows['tests']} | {rows['teachers'][0].get('subject')}
            subject_ids = {r['roster_subject_id'] for r in rows['test_sessions']}
            rows['roster_subjects'] = [r for r in rows['roster_subjects'] if r['id'] in subject_ids or r['name'] in subject_names]
            rows['roster_replicas'] = []
            rows['roster_state'] = []
            for row in rows['teachers']:
                row.pop('hashed_password', None)
                row.pop('auth_nonce', None)
        if not config['students']:
            for name in ('test_sessions', 'student_attempts', 'student_answers', 'event_logs'):
                rows[name] = []
        index = {'app': self.app, 'protocol': 1, 'application_version':settings.APP_VERSION, 'scope': config['scope'], 'owner': int(owner),
                 'schema': schema(), 'options': {k: config[k] for k in ('materials', 'students', 'student_files')},
                 'counts': {k: len(v) for k,v in rows.items()}}
        entries = [('index.json', bytes_source(json_bytes(index)))]
        for table, records in sorted(rows.items()):
            for row in records:
                encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, default=lambda x: x.isoformat() if isinstance(x, datetime) else x, separators=(',', ':')).encode()
                key = row.get('id', row.get('ref'))
                entries.append((f'db/{table}/{key}.json', bytes_source(encoded)))
        files = {}
        roots = {'tests': Path(settings.STATIC_DIR)/'tests', 'uploads': Path(settings.STATIC_DIR)/'uploads'}
        if config['materials']:
            for test in rows['tests']:
                folder = test.get('folder_name')
                if folder:
                    safe_path(folder)
                    root = destination(roots['tests'], folder)
                    if root.exists():
                        for path in root.rglob('*'):
                            if path.is_file():
                                files['tests/'+path.relative_to(roots['tests']).as_posix()] = path
            for table in ('questions', 'answer_options'):
                for row in rows[table]:
                    url = row.get('image_url') or ''
                    for kind in ('uploads','tests'):
                        prefix = '/static/'+kind+'/'
                        if url.startswith(prefix):
                            from urllib.parse import unquote
                            relative = unquote(url.removeprefix(prefix))
                            files[kind+'/'+relative] = destination(roots[kind],relative)
        if config['scope'] == 'server' and config['materials']:
            for name, root in roots.items():
                if root.exists():
                    for path in root.rglob('*'):
                        if path.is_file():
                            files[name+'/'+path.relative_to(root).as_posix()] = path
        for name, path in sorted(files.items()):
            entries.append(('files/'+name, file_source(path)))
        yield entries

    def load(self, config, directory):
        index = json.loads((directory/'index.json').read_text())
        if index.get('app') != self.app or index.get('scope') != config['scope'] or index.get('schema') != schema():
            raise ValueError('Копія несумісна з цією схемою SchoolTest. Установіть сумісну версію сайту')
        rows = {}
        for name in schema():
            rows[name] = [json.loads(p.read_text()) for p in sorted((directory/'db'/name).glob('*.json'))]
            if len(rows[name]) != index['counts'].get(name):
                raise ValueError('Неповний склад БД')
        if config['scope'] == 'personal':
            if len(rows['teachers']) != 1 or rows['teachers'][0]['id'] != index['owner']:
                raise ValueError('Некоректний власник особистої копії')
            if any(r['teacher_id'] != index['owner'] for r in rows['tests']):
                raise ValueError('Копія містить сторонні тести')
        for table in Base.metadata.sorted_tables:
            for row in rows.get(table.name, []):
                if set(row)-set(table.c.keys()):
                    raise ValueError('Невідомі поля БД')
                for col in table.c:
                    if isinstance(col.type, DateTime) and row.get(col.name):
                        row[col.name] = datetime.fromisoformat(row[col.name])
        pairs = []
        for path in (directory/'files').rglob('*') if (directory/'files').exists() else []:
            if path.is_file():
                relative = path.relative_to(directory/'files').as_posix()
                kind, name = relative.split('/', 1)
                if kind not in ('tests', 'uploads'):
                    raise ValueError('Невідомий каталог файлів')
                pairs.append((path, destination(Path(settings.STATIC_DIR)/kind, name)))
        return index, rows, pairs

    def validate(self, owner, config, directory):
        index, rows, pairs = self.load(config, directory)
        return {'records': sum(len(r) for r in rows.values()), 'files': len(pairs), 'options': index['options']}

    def restore(self, owner, config, directory):
        self.authorize(owner, config['scope'])
        owner = self.resolve(owner)
        index, rows, pairs = self.load(config, directory)
        with engine.begin() as conn:
            if engine.dialect.name == 'postgresql':
                conn.execute(text('LOCK TABLE teachers, tests, test_sessions, student_attempts IN ACCESS EXCLUSIVE MODE'))
            conn.execute(text('CREATE TABLE IF NOT EXISTS cloud_restore_receipts (receipt VARCHAR(200) PRIMARY KEY)'))
            receipt = config['repository']+':'+config['_snapshot']
            if conn.execute(text('SELECT receipt FROM cloud_restore_receipts WHERE receipt=:r'), {'r': receipt}).first():
                return {'already_restored': True}
            if conn.execute(select(models.Test.id)).first() or len(conn.execute(select(models.Teacher.id)).all()) > 1:
                raise ValueError('Відновлення дозволене лише на новому встановленні без тестів та інших учителів')
            for path in [target for source,target in pairs]:
                if path.exists():
                    raise ValueError('Файли вже існують. Використайте чисте встановлення')
            personal = config['scope'] == 'personal'
            if personal:
                original = index['owner']
                for records in rows.values():
                    for row in records:
                        if row.get('teacher_id') == original:
                            row['teacher_id'] = int(owner)
                profile = rows['teachers'][0]
                rows['teachers'] = []
                for table in reversed(Base.metadata.sorted_tables):
                    if table.name != 'teachers': conn.execute(table.delete())
                conn.execute(models.Teacher.__table__.update().where(models.Teacher.id==int(owner)).values(
                    full_name=profile['full_name'],subject=profile.get('subject'),classes=profile.get('classes')))
            else:
                for table in reversed(Base.metadata.sorted_tables):
                    conn.execute(table.delete())
            # The receipt commits with the rows, making restart/retry idempotent.
            with install_files(pairs):
                for table in Base.metadata.sorted_tables:
                    if rows.get(table.name):
                        conn.execute(table.insert(), rows[table.name])
                if engine.dialect.name == 'postgresql':
                    for table in Base.metadata.sorted_tables:
                        if 'id' in table.c:
                            conn.execute(text("SELECT setval(pg_get_serial_sequence(:t,'id'), COALESCE((SELECT MAX(id) FROM \""+table.name+"\"),1), EXISTS(SELECT 1 FROM \""+table.name+"\")) WHERE pg_get_serial_sequence(:t,'id') IS NOT NULL"), {'t': table.name})
                conn.execute(text('INSERT INTO cloud_restore_receipts(receipt) VALUES(:r)'), {'r': receipt})
                # Commit inside the file rollback guard.
                conn.commit()
        return {'records': sum(len(r) for r in rows.values()), 'files': len(pairs)}

    def notify(self, owner, title, body):
        # Persistent notices are merged into /teacher/notifications.
        pass

    def notify_restored(self,title,body):
        with SessionLocal() as db:
            for teacher in db.query(models.Teacher).filter_by(is_active=True):
                if can_manage_updates(db,teacher): state().notice(self.owner(teacher),title,body)
