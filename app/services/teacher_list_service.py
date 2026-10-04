"""Filter and paginate teacher lists in SQL instead of loading every attempt."""

import math

from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload

from app import models


def build_list(db, teacher_id, kind, *, page, per_page, tab, search, class_filter, state):
    tab = "archive" if tab == "archive" else "active"
    per_page = per_page if per_page in (10, 20, 50, 100) else 20
    search = search.strip()[:100]
    state = state if state in ("all", "running", "finished") else "all"
    test = models.Test
    session = models.TestSession
    if kind == "tests":
        base = db.query(test).filter(test.teacher_id == teacher_id)
        archive_field = test.is_archived
    else:
        base = db.query(session).join(test).filter(test.teacher_id == teacher_id)
        archive_field = session.is_archived
    active_count = base.filter(archive_field.is_(False)).count()
    archive_count = base.filter(archive_field.is_(True)).count()
    classes = [row[0] for row in db.query(test.class_name).filter(
        test.teacher_id == teacher_id, test.class_name.is_not(None), test.class_name != ""
    ).distinct().order_by(test.class_name).all()]
    query = base.filter(archive_field.is_(tab == "archive"))
    if search:
        sqlite = db.bind.dialect.name == "sqlite"
        search_value = search.casefold() if sqlite else search
        pattern = "%" + search_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        def contains(column):
            return func.unicode_casefold(column).like(pattern, escape="\\") if sqlite else column.ilike(pattern, escape="\\")
        terms = [contains(test.title), contains(test.subject)]
        if kind == "sessions":
            terms.append(contains(session.access_code))
        query = query.filter(or_(*terms))
    if class_filter:
        query = query.filter(test.class_name == class_filter)
    if kind == "sessions" and state != "all":
        query = query.filter(session.is_active.is_(state == "running"))
    total = query.count()
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))
    if kind == "tests":
        rows = query.order_by(test.created_at.desc(), test.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
        ids = [item.id for item in rows]
        counts = dict(db.query(models.Question.test_id, func.count(models.Question.id)).filter(
            models.Question.test_id.in_(ids)
        ).group_by(models.Question.test_id).all()) if ids else {}
        running = dict(db.query(session.test_id, func.count(session.id)).filter(
            session.test_id.in_(ids), session.is_active.is_(True)
        ).group_by(session.test_id).all()) if ids else {}
        items = [{"test": item, "question_count": counts.get(item.id, 0),
                  "active_session_count": running.get(item.id, 0)} for item in rows]
    else:
        rows = query.options(joinedload(session.test)).order_by(
            session.is_active.desc(), session.started_at.desc(), session.id.desc()
        ).offset((page - 1) * per_page).limit(per_page).all()
        ids = [item.id for item in rows]
        attempt = models.StudentAttempt
        counts = {row[0]: (row[1], row[2]) for row in db.query(
            attempt.session_id, func.count(attempt.id), func.sum(case((attempt.status.in_([
                models.AttemptStatus.finished, models.AttemptStatus.timeout, models.AttemptStatus.stopped
            ]), 1), else_=0))
        ).filter(attempt.session_id.in_(ids)).group_by(attempt.session_id).all()} if ids else {}
        items = [{"session": item, "attempt_count": counts.get(item.id, (0, 0))[0],
                  "finished_count": counts.get(item.id, (0, 0))[1]} for item in rows]
    return {"tests_with_count" if kind == "tests" else "sessions_data": items,
            "page": kind, "current_page": page, "total_pages": pages, "total_items": total,
            "per_page": per_page, "tab": tab, "search": search, "class_filter": class_filter,
            "state": state, "classes": classes, "active_count": active_count, "archive_count": archive_count}
