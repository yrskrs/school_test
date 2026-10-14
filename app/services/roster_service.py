"""Read local and teacher-scoped imported rosters without changing sync state."""
from types import SimpleNamespace

from app import models
from school_sync.journal_roster import historical_class_names, imported_classes


def roster_metadata(db):
    state = db.get(models.RosterState, 1)
    return SimpleNamespace(meta=(state.data or {}) if state else {})


def teacher_roster_classes(db, teacher_id, *, active_only=True):
    store = roster_metadata(db)
    own = imported_classes(store, teacher_id)
    imported = {row['class_id'] for row in store.meta.get('journal_imports', {}).values()}
    # Native school lists are shared, as in the roster editor. Journal copies
    # belong to the owner of the API key unless explicitly imported by both.
    query = db.query(models.RosterClass).filter(~models.RosterClass.id.in_(imported - own))
    if active_only:
        query = query.filter(models.RosterClass.active.is_(True))
    return [group for group in query.order_by(models.RosterClass.grade_level, models.RosterClass.name).all()
            if not (group.letter == 'ПК' and group.name.endswith(' (Позакласні)'))]


def resolve_roster_class(db, test, explicit_class_id=None):
    if explicit_class_id:
        group = db.get(models.RosterClass, explicit_class_id)
        if group is None:
            return None, 'Список класу цієї сесії недоступний. Зверніться до вчителя.'
    else:
        name = (test.class_name or '').strip()
        if not name:
            return None, ''
        store = roster_metadata(db)
        own = imported_classes(store, test.teacher_id)
        populated = {row[0] for row in db.query(models.RosterStudent.class_id).distinct()}
        groups = [group for group in teacher_roster_classes(db, test.teacher_id, active_only=False)
                  if group.id in own or group.id in populated]
        # An exact current label takes priority over labels retained for history.
        candidates = [group for group in groups if group.name == name]
        if not candidates:
            candidates = [group for group in groups if group.id in own and
                          name in historical_class_names(store, test.teacher_id, group.id, group.name)]
        if len(candidates) > 1:
            return None, 'Назва тесту відповідає кільком спискам класів. Учитель має явно прив’язати сесію до класу.'
        group = candidates[0] if candidates else None
    if group and not group.active:
        return group, 'Цей клас неактивний. Зверніться до вчителя.'
    return group, ''
