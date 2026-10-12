"""Routes for system updates: check GitHub releases, multi-version changelogs,
autonomous update runner, status monitoring, and rollback.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import models
from app.deps import get_current_teacher
from app.services.updates import (
    check_updates_available,
    get_current_local_version,
    get_update_status,
    start_rollback,
    start_update,
)
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["updates"])


def _verify_admin_access(teacher: models.Teacher):
    """Enforce strict super-administrator access for system update operations."""
    if not teacher or teacher.username != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ до керування оновленнями системи дозволено лише адміністратору (admin)."
        )


@router.get("/updates", include_in_schema=False)
async def updates_redirect(request: Request):
    """Convenience redirect to the teacher updates page."""
    return RedirectResponse(url="/teacher/updates", status_code=303)


@router.get("/teacher/updates", response_class=HTMLResponse)
async def updates_page(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher)
):
    """Render the System Updates page for the administrator."""
    _verify_admin_access(teacher)

    current_version = get_current_local_version()
    status_info = get_update_status()

    return templates.TemplateResponse(
        request,
        "updates.html",
        {
            "teacher": teacher,
            "current_version": current_version,
            "status_info": status_info,
            "can_update": True,
        }
    )


@router.get("/teacher/updates/api/check")
@router.post("/teacher/updates/api/check")
async def api_check_updates(
    teacher: models.Teacher = Depends(get_current_teacher)
):
    """Query GitHub and local releases for available updates and intermediate changelogs."""
    _verify_admin_access(teacher)
    result = check_updates_available()
    return JSONResponse(result)


@router.post("/teacher/updates/api/start")
async def api_start_update(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher)
):
    """Start the update process in the autonomous updater daemon."""
    _verify_admin_access(teacher)

    target_ref = "origin/main"
    target_ver = None

    try:
        body = await request.json()
        target_ref = body.get("ref", "origin/main")
        target_ver = body.get("version")
    except Exception:
        form = await request.form()
        target_ref = form.get("ref", "origin/main")
        target_ver = form.get("version")

    result = start_update(target_ref=target_ref, target_ver=target_ver)
    return JSONResponse(result)


@router.get("/teacher/updates/api/status")
async def api_update_status(
    teacher: models.Teacher = Depends(get_current_teacher)
):
    """Get the current update status, progress percentage, and streaming execution logs."""
    _verify_admin_access(teacher)
    status_info = get_update_status()
    return JSONResponse(status_info)


@router.post("/teacher/updates/api/rollback")
async def api_rollback(
    request: Request,
    teacher: models.Teacher = Depends(get_current_teacher)
):
    """Trigger rollback to previous backup in the updater daemon."""
    _verify_admin_access(teacher)

    backup_dir = None
    try:
        body = await request.json()
        backup_dir = body.get("backup_dir")
    except Exception:
        form = await request.form()
        backup_dir = form.get("backup_dir")

    result = start_rollback(backup_dir=backup_dir)
    return JSONResponse(result)
