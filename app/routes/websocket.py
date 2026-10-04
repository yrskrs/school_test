from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import ws_manager
from app.database import SessionLocal
from app import crud
from app.security import get_student_attempt_id, get_teacher_session
from urllib.parse import urlsplit

router = APIRouter()


def same_origin(websocket):
    origin = websocket.headers.get("origin")
    return not origin or urlsplit(origin).netloc == websocket.headers.get("host")


@router.websocket("/ws/monitor/{session_id}")
async def websocket_monitor(websocket: WebSocket, session_id: int):
    """
    WebSocket-ендпойнт для вчителя на сторінці моніторингу.
    Підключається до кімнати сесії і слухає події учнів.
    """
    session_cookie = get_teacher_session(websocket)
    with SessionLocal() as db:
        teacher = crud.get_teacher_by_id(db, session_cookie["id"]) if session_cookie else None
        session = crud.get_session_by_id(db, session_id)
        authorized = teacher and teacher.is_active and session and session.test.teacher_id == teacher.id
    if not authorized or not same_origin(websocket):
        await websocket.close(code=1008)
        return
    await ws_manager.connect(websocket, session_id)
    try:
        # Надсилаємо підтвердження підключення
        await websocket.send_json({
            "event": "connected",
            "session_id": session_id,
            "message": "Моніторинг підключено",
        })
        # Чекаємо повідомлення (ping / close) від клієнта
        while True:
            data = await websocket.receive_text()
            # Можна обробляти ping-pong або команди від вчителя
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, session_id)
    except Exception:
        ws_manager.disconnect(websocket, session_id)


@router.websocket("/ws/student/{attempt_id}")
async def websocket_student(websocket: WebSocket, attempt_id: int):
    """
    WebSocket-ендпойнт для учня на сторінці проходження тесту.
    Слухає події паузи/зупинки від вчителя.
    """
    if get_student_attempt_id(websocket) != attempt_id or not same_origin(websocket):
        await websocket.close(code=1008)
        return
    with SessionLocal() as db:
        if not crud.get_attempt_by_id(db, attempt_id):
            await websocket.close(code=1008)
            return
    await ws_manager.connect_student(websocket, attempt_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect_student(attempt_id, websocket)
    except Exception:
        ws_manager.disconnect_student(attempt_id, websocket)
