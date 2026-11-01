"""Teacher cloud backup settings with explicit CSRF protection."""
import hashlib
import hmac
import re
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from app.config import settings
from app.deps import get_current_teacher
from app.templating import templates
from app.services.cloud_backups import Adapter, state
from cloud_backup.control import page_data, save_config, enqueue

router = APIRouter(tags=['backups'])


def csrf(teacher):
    return hmac.new(settings.SECRET_KEY.encode(), f'backup:{teacher.id}:{teacher.auth_nonce}'.encode(), hashlib.sha256).hexdigest()


def scope_of(request):
    scope = request.query_params.get('scope', 'personal')
    if scope not in ('personal','server'): raise HTTPException(400, 'Некоректний обсяг')
    return scope


@router.get('/teacher/backups')
def page(request:Request, teacher=Depends(get_current_teacher)):
    scope = scope_of(request)
    try: Adapter().authorize(Adapter.owner(teacher), scope)
    except ValueError as error: raise HTTPException(403,str(error))
    data = page_data(state(),Adapter.owner(teacher),scope)
    return templates.TemplateResponse(request, 'backups.html', {**data, 'teacher':teacher,
        'backup_csrf':csrf(teacher), 'backup_admin':teacher.can_manage_updates,
        'backup_base':'/teacher/backups', 'backup_error':request.query_params.get('error',''),
        'backup_message':request.query_params.get('message','')})


@router.post('/teacher/backups')
async def action(request:Request, teacher=Depends(get_current_teacher)):
    from urllib.parse import urlencode
    scope = scope_of(request)
    form = await request.form()
    if not hmac.compare_digest(str(form.get('csrfmiddlewaretoken','')),csrf(teacher)):
        raise HTTPException(403,'Оновіть сторінку: недійсний CSRF token')
    try:
        Adapter().authorize(Adapter.owner(teacher),scope)
        operation = form.get('action','save')
        if operation == 'save':
            save_config(state(),Adapter.owner(teacher),scope,form)
            message = 'Налаштування збережено. Збережіть ідентифікатор сховища поза сервером.'
        elif operation == 'read':
            state().mark_seen(Adapter.owner(teacher))
            message = 'Сповіщення позначено прочитаними'
        else:
            enqueue(state(),Adapter.owner(teacher),scope,operation,form)
            message = 'Операцію додано до черги. Підсумок з’явиться у центрі сповіщень.'
        query = {'scope':scope,'message':message}
    except ValueError as error:
        query = {'scope':scope,'error':str(error)}
    return RedirectResponse('/teacher/backups?'+urlencode(query), status_code=303)


@router.get('/teacher/backups/status')
def status(request:Request, teacher=Depends(get_current_teacher)):
    scope = scope_of(request)
    try: Adapter().authorize(Adapter.owner(teacher),scope)
    except ValueError as error: raise HTTPException(403,str(error))
    return page_data(state(),Adapter.owner(teacher),scope)


@router.get('/teacher/backups/download/{job}')
def download(job:str, teacher=Depends(get_current_teacher)):
    if not re.fullmatch('[a-f0-9]{32}',job): raise HTTPException(404)
    store = state()
    records = store.history(Adapter.owner(teacher))
    for record in records:
        if record['id'] == job and record.get('scope') == 'server':
            try: Adapter().authorize(Adapter.owner(teacher),'server')
            except ValueError: raise HTTPException(403)
    if not any(r['id']==job and r['kind']=='download' and r['status']=='completed' for r in store.history(Adapter.owner(teacher))):
        raise HTTPException(404)
    target = store.root/(job+'.zip')
    if not target.is_file() or target.stat().st_mtime<time.time()-86400: raise HTTPException(404,'Архів уже видалено. Підготуйте завантаження повторно')
    return FileResponse(target,media_type='application/zip',filename='schooltest-backup.zip',headers={'Cache-Control':'no-store'})
