"""Optional /test entry point for a shared-host HTTPS Funnel."""
import os
from starlette.middleware.gzip import GZipMiddleware
from app.main import app as original_app
from app.funnel import HTTPConcurrencyGuard, PrefixApp

app = HTTPConcurrencyGuard(
    # Prefix rewriting needs identity bytes; compress only its final response.
    GZipMiddleware(PrefixApp(original_app), minimum_size=1000, compresslevel=5),
    limit=int(os.environ.get("SCHOOLTEST_HTTP_CONCURRENCY", "20")),
)
