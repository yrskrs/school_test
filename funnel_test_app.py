"""Optional /test entry point for a shared-host HTTPS Funnel."""
import os
from app.main import app as original_app
from app.funnel import HTTPConcurrencyGuard, PrefixApp

app = HTTPConcurrencyGuard(
    PrefixApp(original_app),
    limit=int(os.environ.get("SCHOOLTEST_HTTP_CONCURRENCY", "20")),
)
