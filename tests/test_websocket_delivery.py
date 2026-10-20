"""A stalled browser must not hold acknowledgement of an answer or stop event."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.websocket_manager import ConnectionManager


class Socket:
    def __init__(self, stalled=False):
        self.stalled = stalled
        self.messages = []
        self.delivered = asyncio.Event()

    async def send_text(self, message):
        if self.stalled:
            await asyncio.Event().wait()
        self.messages.append(json.loads(message))
        self.delivered.set()

    async def send_json(self, message):
        await self.send_text(json.dumps(message))


class WebsocketDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_monitor_does_not_delay_healthy_monitor(self):
        manager = ConnectionManager()
        manager.send_timeout = .05
        stalled, healthy = Socket(True), Socket()
        manager._active[1] = [stalled, healthy]
        task = asyncio.create_task(manager.broadcast(1, {'event': 'stop'}))
        await healthy.delivered.wait()
        self.assertFalse(task.done())
        await task
        self.assertEqual(healthy.messages, [{'event': 'stop'}])
        self.assertEqual(manager._active[1], [healthy])

    async def test_stalled_student_is_removed_without_losing_replacement(self):
        manager = ConnectionManager()
        manager.send_timeout = .02
        stalled, replacement = Socket(True), Socket()
        manager._student_websockets[1] = stalled
        task = asyncio.create_task(manager.send_to_student(1, {'event': 'stop'}))
        await asyncio.sleep(0)
        manager._student_websockets[1] = replacement
        await task
        self.assertIs(manager._student_websockets[1], replacement)
        await manager.send_to_student(1, {'event': 'stop'})
        self.assertEqual(replacement.messages, [{'event': 'stop'}])

    async def test_stalled_student_send_finishes_and_disconnects(self):
        manager = ConnectionManager()
        manager.send_timeout = .02
        manager._student_websockets[1] = Socket(True)
        await asyncio.wait_for(manager.send_to_student(1, {'event': 'stop'}), timeout=1)
        self.assertNotIn(1, manager._student_websockets)


if __name__ == '__main__':
    unittest.main()
