"""Keep LAN routes unchanged; serve SchoolTest under /test on Funnel."""
import re
import json
import asyncio
import os
import weakref
from urllib.parse import urlsplit, urlunsplit

PUBLIC_HOST = os.environ.get("SCHOOLTEST_PUBLIC_HOST", "").strip().lower()
PREFIX = "/test"
LINK = re.compile(rb"([\"'`])(/(?:teacher|student|api|static|setup|ws|updates|backups|integrations|favicon\.ico)(?=[/?#\"'`])[^\"'`\s<>]*)(?=[\"'`])")
ROOT_LINK = re.compile(rb"((?:href|src|action)=[\"'])/([\"'])", re.IGNORECASE)
HOST_LINK = re.compile(rb"(\$\{(?:window\.)?location\.(?:host|origin)\})(/(?:ws|api|student|teacher|static)(?=/))")


def map_media_urls(data, public=True):
    """Translate media fields only; keep question text and external URLs intact."""
    if isinstance(data, list):
        return [map_media_urls(value, public) for value in data]
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ("url", "image_url") and isinstance(value, str):
                if public and value.startswith("/static/"):
                    value = PREFIX + value
                elif not public and value.startswith(PREFIX + "/static/"):
                    value = value[len(PREFIX):]
                result[key] = value
            else:
                result[key] = map_media_urls(value, public)
        return result
    return data


class PrefixApp:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode().split(":")[0].lower()
        if host != PUBLIC_HOST or scope["type"] not in ("http", "websocket"):
            return await self.application(scope, receive, send)
        scope = dict(scope)
        path = scope.get("path", "/")
        if path == PREFIX or path.startswith(PREFIX + "/"):
            path = path[len(PREFIX):] or "/"
        scope["root_path"] = PREFIX
        scope["path"] = PREFIX + path
        scope["raw_path"] = scope["path"].encode()
        scope["scheme"] = "wss" if scope["type"] == "websocket" else "https"
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"accept-encoding"]
        if scope["type"] == "websocket":
            return await self.application(scope, receive, send)

        start = None
        body = []
        rewrite = False

        async def wrapped_send(message):
            nonlocal start, rewrite
            if message["type"] == "http.response.start":
                start = dict(message)
                outgoing = []
                for key, value in message["headers"]:
                    if key.lower() == b"location":
                        url = value.decode("latin1")
                        parsed = urlsplit(url)
                        if (not parsed.netloc and url.startswith("/") and not url.startswith("//")) or parsed.hostname == PUBLIC_HOST:
                            path = parsed.path
                            if path != PREFIX and not path.startswith(PREFIX + "/"):
                                path = PREFIX + path
                            url = urlunsplit(("https" if parsed.netloc else "", parsed.netloc, path, parsed.query, parsed.fragment))
                            value = url.encode("latin1")
                    elif key.lower() == b"set-cookie":
                        value = re.sub(rb"(?i)(;\s*path=)/(?=;|$)", rb"\1/test", value)
                    outgoing.append((key, value))
                start["headers"] = outgoing
                values = dict(outgoing)
                content_type = values.get(b"content-type", b"").lower()
                rewrite = any(t in content_type for t in (b"text/html", b"javascript", b"text/css", b"application/json")) and b"content-encoding" not in values and b"attachment" not in values.get(b"content-disposition", b"").lower()
                if not rewrite:
                    await send(start)
            elif message["type"] == "http.response.body" and rewrite:
                body.append(message.get("body", b""))
                if not message.get("more_body", False):
                    payload = b"".join(body)
                    kind = dict(start["headers"]).get(b"content-type", b"").lower()
                    if b"application/json" in kind:
                        try:
                            data = map_media_urls(json.loads(payload))
                            target = data.get("redirect") if isinstance(data, dict) else None
                            if isinstance(target, str) and target.startswith("/") and not target.startswith("//") and target != PREFIX and not target.startswith(PREFIX + "/"):
                                data["redirect"] = PREFIX + target
                            payload = json.dumps(data, ensure_ascii=False).encode()
                        except (ValueError, UnicodeError):
                            pass
                    else:
                        payload = LINK.sub(lambda m: m[1] + PREFIX.encode() + m[2], payload)
                        payload = HOST_LINK.sub(lambda m: m[1] + PREFIX.encode() + m[2], payload)
                        if b"text/html" in kind:
                            payload = ROOT_LINK.sub(lambda m: m[1] + PREFIX.encode() + b"/" + m[2], payload)
                    start["headers"] = [(k, v) for k, v in start["headers"] if k.lower() not in (b"content-length", b"etag")]
                    start["headers"].append((b"content-length", str(len(payload)).encode()))
                    await send(start)
                    await send({"type": "http.response.body", "body": payload})
            else:
                await send(message)

        await self.application(scope, receive, wrapped_send)


class HTTPConcurrencyGuard:
    """Queue HTTP work before sync DB dependencies can exhaust their pool.

    WebSockets only borrow a DB connection during authentication and must not
    occupy an HTTP slot for the lifetime of the connection.
    """
    def __init__(self, application, limit=20):
        self.application = application
        self.limit = limit
        self._semaphores = weakref.WeakKeyDictionary()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = self._semaphores[loop] = asyncio.Semaphore(self.limit)
        async with semaphore:
            return await self.application(scope, receive, send)

