from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/monitor/{session_id}")
async def websocket_monitor(websocket: WebSocket, session_id: int):
    """
    WebSocket-ендпойнт для вчителя на сторінці моніторингу.
    Підключається до кімнати сесії і слухає події учнів.
    """
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
    await ws_manager.connect_student(websocket, attempt_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect_student(attempt_id)
    except Exception:
        ws_manager.disconnect_student(attempt_id)
