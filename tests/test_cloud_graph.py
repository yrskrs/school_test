"""End-to-end logical recovery tests on disposable SQLite and synthetic files."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import text

WORK = tempfile.TemporaryDirectory(prefix='schooltest-cloud-graph-')
AUDIT_URL='postgresql+psycopg://audit:audit-synthetic@cloud-backup-audit-postgres:5432/schooltest_cloud_audit'
TEST_URL=AUDIT_URL if os.environ.get('CLOUD_BACKUP_TEST_POSTGRES')=='1' else 'sqlite:///'+WORK.name+'/db.sqlite3'
os.environ.update(PYTHON_DOTENV_DISABLED='1', DATABASE_URL=TEST_URL, SECRET_KEY='synthetic-cloud-tests', CLOUD_BACKUP_ROOT=WORK.name+'/control')
from app.config import settings
settings.STATIC_DIR = WORK.name+'/static'
from app import models
from app.database import Base, SessionLocal, engine
from app.services.cloud_backups import Adapter
from cloud_backup.archive import Repository
from cloud_backup.staging import recovery
from test_cloud_archive import MemoryRemote


class GraphTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        with engine.begin() as c: c.execute(text('DROP TABLE IF EXISTS cloud_restore_receipts'))
        Base.metadata.create_all(engine)
        self.config = {'repository':'a'*32,'scope':'personal','format':'incremental','materials':False,'students':True,'student_files':True}
        with SessionLocal() as db:
            db.add_all([models.Teacher(id=1,username='admin',full_name='Admin',hashed_password='secret1'),models.Teacher(id=2,username='teacher',full_name='Teacher',hashed_password='secret2')]);db.flush()
            db.add_all([models.Test(id=10,teacher_id=2,title='Own'),models.Test(id=20,teacher_id=1,title='Other')]);db.flush()
            db.add(models.Question(id=11,test_id=10,question_text='Q',question_type=models.QuestionType.single_choice));db.flush()
            db.add(models.AnswerOption(id=12,question_id=11,option_text='Yes',is_correct=True));db.flush()
            db.add(models.TestSession(id=13,test_id=10,access_code='CODE13'));db.flush()
            db.add(models.StudentAttempt(id=14,session_id=13,student_name='Учень',score=1,max_score=1));db.flush()
            db.add(models.StudentAnswer(id=15,attempt_id=14,question_id=11,selected_options_json='[12]',is_correct=True));db.commit()
        self.adapter=Adapter()

    def capture(self, owner=2):
        remote=MemoryRemote();repo=Repository(remote,'schooltest','a'*32,'password')
        with self.adapter.snapshot(owner,self.config) as entries:
            job=repo.backup(entries,scope=self.config['scope'])
        return repo,job['snapshot']

    def clean_target(self):
        Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
        with SessionLocal() as db:
            db.add(models.Teacher(id=1,username='new',full_name='New',hashed_password='new-secret'));db.commit()

    def test_personal_isolated_and_restore_remaps_owner_keeps_answers_and_is_idempotent(self):
        repo,snapshot=self.capture()
        rows=dict(repo.read(snapshot))
        self.assertNotIn('db/tests/20.json',rows)
        self.assertNotIn(b'secret2',b''.join(rows.values()))
        self.clean_target()
        self.config['_snapshot']=snapshot
        with recovery(WORK.name,repo,snapshot,'personal') as directory:
            self.assertEqual(self.adapter.validate(1,self.config,directory)['records'], 7)
            self.adapter.restore(1,self.config,directory)
            self.assertTrue(self.adapter.restore(1,self.config,directory)['already_restored'])
        with SessionLocal() as db:
            self.assertEqual(db.get(models.Test,10).teacher_id,1)
            self.assertEqual(db.get(models.StudentAnswer,15).selected_options_json,'[12]')
            self.assertEqual(db.get(models.Teacher,1).hashed_password,'new-secret')
            self.assertEqual(db.get(models.StudentAttempt,14).student_name,'Учень')

    def test_existing_work_blocks_restore_without_mutation(self):
        repo,snapshot=self.capture();self.config['_snapshot']=snapshot
        with recovery(WORK.name,repo,snapshot,'personal') as directory:
            with self.assertRaises(ValueError): self.adapter.restore(2,self.config,directory)
        with SessionLocal() as db: self.assertEqual(db.query(models.Test).count(),2)

    def test_server_restores_both_teachers_and_accounts(self):
        self.config['scope']='server'
        repo,snapshot=self.capture(1);self.clean_target();self.config['_snapshot']=snapshot
        with recovery(WORK.name,repo,snapshot,'server') as directory:
            self.adapter.restore(1,self.config,directory)
        with SessionLocal() as db:
            self.assertEqual(db.query(models.Test).count(),2)
            self.assertEqual(db.get(models.Teacher,2).hashed_password,'secret2')

    def test_teacher_cannot_create_full_server_copy(self):
        self.config['scope']='server'
        with self.assertRaises(ValueError): self.capture(2)

    def test_data_only_excludes_student_answers(self):
        self.config.update(students=False,student_files=False)
        repo,snapshot=self.capture()
        self.assertFalse(any(name.startswith('db/student_') for name,value in repo.read(snapshot)))

    def test_outside_class_student_keeps_required_roster_dependencies(self):
        with SessionLocal() as db:
            db.add(models.RosterClass(id=91,name='OtherClass'));db.flush()
            db.add(models.RosterStudent(id=92,class_id=91,first_name='Іван',last_name='Учень'));db.flush()
            db.get(models.StudentAttempt,14).roster_student_id=92;db.commit()
        repo,snapshot=self.capture();contents=dict(repo.read(snapshot))
        self.assertIn('db/roster_students/92.json',contents)
        self.assertIn('db/roster_classes/91.json',contents)
        self.clean_target();self.config['_snapshot']=snapshot
        with recovery(WORK.name,repo,snapshot,'personal') as directory:self.adapter.restore(1,self.config,directory)
        with SessionLocal() as db:self.assertEqual(db.get(models.StudentAttempt,14).roster_student_id,92)

    def test_portable_password_zip_restores_without_cloud_access(self):
        from cloud_backup.cli import extract_portable
        from cloud_backup.archive import file_digest
        repo=Repository(MemoryRemote(),'schooltest','a'*32,'zip-password')
        with self.adapter.snapshot(2,self.config) as entries:
            result=repo.backup(entries,archive_format='zip')
        archive=Path(WORK.name)/'portable.zip'
        archive.write_bytes(repo.remote.get('snapshots/'+result['snapshot']))
        self.clean_target();self.config['_snapshot']='offline-'+file_digest(archive)
        with tempfile.TemporaryDirectory(dir=WORK.name) as work:
            extract_portable(archive,work,'zip-password')
            self.adapter.restore(1,self.config,Path(work))
        with SessionLocal() as db:self.assertEqual(db.get(models.StudentAnswer,15).selected_options_json,'[12]')

    def test_http_settings_csrf_and_secret_redaction(self):
        import asyncio,re
        from urllib.parse import urlencode
        Path(settings.STATIC_DIR).mkdir(parents=True,exist_ok=True)
        from app.main import app
        from app.security import create_teacher_session
        from fastapi.responses import Response
        response=Response();create_teacher_session(response,2,'teacher')
        cookie=response.headers['set-cookie'].split(';',1)[0]
        async def http(method,path,data=None):
            path,_,query=path.partition('?')
            payload=urlencode(data).encode() if data is not None else b''
            headers=[(b'host',b'checks.local'),(b'cookie',cookie.encode()),(b'accept',b'text/html')]
            if data is not None:headers.append((b'content-type',b'application/x-www-form-urlencoded'))
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':method,'scheme':'http','path':path,'raw_path':path.encode(),'query_string':query.encode(),'root_path':'','headers':headers,'client':('127.0.0.1',1),'server':('checks.local',80)}
            messages=[];consumed=False;complete=asyncio.Event()
            async def receive():
                nonlocal consumed
                if not consumed:
                    consumed=True;return {'type':'http.request','body':payload,'more_body':False}
                await complete.wait();return {'type':'http.disconnect'}
            async def send(message):
                messages.append(message)
                if message['type']=='http.response.body' and not message.get('more_body',False):complete.set()
            await asyncio.wait_for(app(scope,receive,send),timeout=10)
            status=next(m['status'] for m in messages if m['type']=='http.response.start')
            body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body').decode()
            return status,body
        async def check():
            status,body=await http('GET','/teacher/backups')
            self.assertEqual(status,200,body[:300]);self.assertIn('Інкрементальне сховище',body)
            self.assertEqual((await http('POST','/teacher/backups',{'action':'save'}))[0],403)
            token=re.search(r'name="csrfmiddlewaretoken" value="([a-f0-9]+)"',body)[1]
            status,_=await http('POST','/teacher/backups',{'action':'save','csrfmiddlewaretoken':token,'provider':'nextcloud','url':'https://cloud.example','username':'teacher','app_password':'private-cloud-pass','password':'private-archive-pass','materials':'on','students':'on','student_files':'on'})
            self.assertEqual(status,303)
            status,body=await http('GET','/teacher/backups/status')
            self.assertEqual(status,200);self.assertNotIn('private-cloud-pass',body);self.assertNotIn('private-archive-pass',body)
            self.assertEqual((await http('GET','/teacher/backups?scope=server'))[0],403)
            self.assertEqual((await http('GET','/teacher/backups/download/'+'a'*32))[0],404)
        asyncio.run(check())

if __name__=='__main__': unittest.main()
