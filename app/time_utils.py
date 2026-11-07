"""Stored naive timestamps use the original zone; display always uses Kyiv."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings

KYIV = ZoneInfo('Europe/Kyiv')


def stored_now():
    """Keep existing naive columns consistent, independently of the host TZ."""
    return datetime.now(ZoneInfo(settings.LEGACY_RESULT_TIMEZONE)).replace(tzinfo=None)


def aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(settings.LEGACY_RESULT_TIMEZONE))
    return value


def kyiv_time(value):
    return aware(value).astimezone(KYIV) if value is not None else None


def format_kyiv(value, fmt='%d.%m.%Y %H:%M'):
    return kyiv_time(value).strftime(fmt) if value is not None else '—'


def timestamp_iso(value):
    return aware(value).astimezone(timezone.utc).isoformat() if value is not None else None


def stored_from_kyiv(value):
    return value.replace(tzinfo=KYIV).astimezone(
        ZoneInfo(settings.LEGACY_RESULT_TIMEZONE)).replace(tzinfo=None)


def format_epoch_kyiv(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Filesystem epoch is an instant, never a host-local calendar value."""
    return datetime.fromtimestamp(value, KYIV).strftime(fmt)


def format_utc_audit(value, fmt='%d.%m.%Y %H:%M'):
    """Integration audit strings were written in UTC, including old naive ones."""
    if not value:
        return '—'
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KYIV).strftime(fmt)
    except (ValueError, TypeError, AttributeError):
        return str(value)
