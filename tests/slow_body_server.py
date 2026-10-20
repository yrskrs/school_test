"""Disposable HTTP peer: headers arrive immediately, body takes 30 seconds."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def respond(self):
        if self.command == 'POST':
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
        body = b'{"status":"in_progress"}'
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.flush()
        time.sleep(30)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = respond
    do_POST = respond


ThreadingHTTPServer(('0.0.0.0', 8765), Handler).serve_forever()
