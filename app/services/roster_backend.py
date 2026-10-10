import asyncio
import copy
import time
from app import models
from app.database import SessionLocal
from app.config import settings
from school_sync.core import Engine, SyncError
from school_sync.transport import fetch


class Store:
    def __init__(self, db):
        self.db = db
        state = db.query(models.RosterState).filter_by(id=1).with_for_update().first()
        if not state:
            state = models.RosterState(id=1, data={})
            db.add(state)
            db.flush()
        self.state, self.meta = state, copy.deepcopy(state.data)
        self.rows = {row.ref: copy.deepcopy(row.data) for row in db.query(models.RosterReplica).all()}

    def all(self):
        return copy.deepcopy(list(self.rows.values()))

    def get(self, ref):
        return copy.deepcopy(self.rows.get(ref))

    def put(self, record):
        row = self.db.get(models.RosterReplica, record['ref'])
        if row is None:
            self.db.add(models.RosterReplica(ref=record['ref'], data=copy.deepcopy(record)))
        else:
            row.data = copy.deepcopy(record)
        self.db.flush()
        self.rows[record['ref']] = copy.deepcopy(record)


class Native:
    def __init__(self, db):
        self.db = db

    def students(self, class_id):
        return [row.id for row in self.db.query(models.RosterStudent).filter_by(class_id=class_id).all()]

    def read(self, kind, native_id):
        row = self.db.get(models.RosterClass if kind == 'class' else models.RosterStudent, native_id)
        if row is None:
            return None
        if kind == 'class':
            return {'name': row.name, 'grade_level': row.grade_level, 'letter': row.letter, 'active': row.active}
        return {'name': row.full_name(), 'first_name': row.first_name, 'last_name': row.last_name,
            'middle_name': row.middle_name or '', 'native_class_id': row.class_id, 'active': row.active}

    def collision(self, kind, payload):
        if kind == 'class':
            return self.db.query(models.RosterClass).filter_by(name=payload['name']).first() is not None
        return any(row.full_name().casefold() == payload['name'].casefold() for row in self.db.query(models.RosterStudent).filter_by(class_id=payload['native_class_id']).all())

    def write(self, kind, native_id, payload):
        if kind == 'class':
            if len(payload['name']) > 50 or len(payload.get('letter', '')) > 10:
                raise SyncError('Назва класу перевищує допустимий розмір.')
            if self.db.query(models.RosterClass).filter(models.RosterClass.name == payload['name'], models.RosterClass.id != native_id).first():
                raise SyncError('Уже існує інший клас із цією назвою. Потрібне явне зіставлення.')
            row = self.db.get(models.RosterClass, native_id)
            row.name, row.grade_level, row.letter = payload['name'], payload.get('grade_level', row.grade_level), payload.get('letter', row.letter)
        else:
            row = self.db.get(models.RosterStudent, native_id)
            row.first_name, row.last_name, row.middle_name = payload.get('first_name', ''), payload.get('last_name', ''), payload.get('middle_name', '')
            row.class_id = payload['native_class_id']
        row.active = payload['active']
        self.db.flush()

    def create(self, kind, payload):
        if kind == 'class':
            if self.collision(kind, payload) or len(payload['name']) > 50:
                raise SyncError('Клас уже існує або назва завелика. Зіставте місцевий ID.')
            row = models.RosterClass(name=payload['name'], grade_level=payload.get('grade_level', 1), letter=payload.get('letter', ''), active=payload['active'])
        else:
            row = models.RosterStudent(class_id=payload['native_class_id'], first_name=payload.get('first_name', ''), last_name=payload.get('last_name', ''), middle_name=payload.get('middle_name', ''), active=payload['active'])
        self.db.add(row)
        self.db.flush()
        return row.id


def operate(action, db=None):
    owns_db = db is None
    db = db or SessionLocal()
    try:
        store = Store(db)
        engine = Engine(store, Native(db))
        result = action(engine, store)
        store.state.data = copy.deepcopy(store.meta)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_db:
            db.close()


def cycle():
    operate(lambda engine, store: engine.capture())
    for index, peer in enumerate(settings.ROSTER_PEERS):
        peer_id = str(index)
        state = operate(lambda engine, store: store.meta.get('peers', {}).get(peer_id, {}))
        if state.get('next_retry', 0) > time.time():
            continue
        try:
            payload = fetch(peer)
            def receive(engine, store):
                known = store.meta.setdefault('peers', {}).setdefault(peer_id, {})
                duplicate = any(key != peer_id and item.get('origin') == payload.get('origin') for key, item in store.meta['peers'].items())
                if duplicate or payload.get('origin') == engine.origin or (known.get('origin') and known['origin'] != payload.get('origin')):
                    raise SyncError('Ідентичність сервісу змінилася або дублюється. Перевірте налаштування.')
                engine.receive(payload)
                known.update(origin=payload['origin'], failures=0, next_retry=time.time() + settings.ROSTER_SYNC_INTERVAL,
                    last_success=time.time(), last_error='', empty=not payload['records'])
            operate(receive)
        except Exception as error:
            message = str(error) if isinstance(error, SyncError) else 'Обмін тимчасово недоступний. Локальні дані збережено.'
            def failed(engine, store):
                known = store.meta.setdefault('peers', {}).setdefault(peer_id, {})
                failures = min(known.get('failures', 0) + 1, 10)
                known.update(failures=failures, last_error=message, next_retry=time.time() + min(settings.ROSTER_SYNC_INTERVAL * 2 ** (failures - 1), 300))
            operate(failed)


async def worker():
    while True:
        try:
            await asyncio.to_thread(cycle)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Roster retry failed; local data retained.')
        await asyncio.sleep(max(2, settings.ROSTER_SYNC_INTERVAL))
