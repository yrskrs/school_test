import json
from typing import Dict, List, Set

from fastapi import WebSocket


class ConnectionManager:
    """
    Менеджер WebSocket-підключень для сторінки моніторингу.
    Кожна сесія тестування може мати кілька вчителів-глядачів.
    """

    def __init__(self):
        # session_id -> list of connected websockets
        self._active: Dict[int, List[WebSocket]] = {}
        # attempt_id -> active student websocket
        self._student_websockets: Dict[int, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: int) -> None:
        await websocket.accept()
        if session_id not in self._active:
            self._active[session_id] = []
        self._active[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket, session_id: int) -> None:
        connections = self._active.get(session_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self._active.pop(session_id, None)

    async def connect_student(self, websocket: WebSocket, attempt_id: int) -> None:
        await websocket.accept()
        self._student_websockets[attempt_id] = websocket

    def disconnect_student(self, attempt_id: int) -> None:
        self._student_websockets.pop(attempt_id, None)

    async def send_to_student(self, attempt_id: int, event: dict) -> None:
        ws = self._student_websockets.get(attempt_id)
        if ws:
            try:
                await ws.send_json(event)
            except Exception:
                self.disconnect_student(attempt_id)

    async def broadcast(self, session_id: int, event: dict) -> None:
        """Надсилає JSON-подію всім підключеним до сесії."""
        connections = self._active.get(session_id, [])
        dead: List[WebSocket] = []
        message = json.dumps(event, ensure_ascii=False, default=str)
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, session_id)

    def session_connection_count(self, session_id: int) -> int:
        return len(self._active.get(session_id, []))


ws_manager = ConnectionManager()
