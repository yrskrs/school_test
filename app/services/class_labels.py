"""Recognize conventional class labels without guessing custom group names."""
import re
import unicodedata

_LOOKALIKES = str.maketrans('ABCEHIKMOPTX', 'АВСЕНІКМОРТХ')


def class_fields(name):
    label = unicodedata.normalize('NFKC', name or '').strip().upper().translate(_LOOKALIKES)
    match = re.fullmatch(r'(1[0-2]|[1-9])\s*[-–—._]?\s*[«"\']?([А-ЯІЇЄҐ]?)\s*[»"\']?', label)
    return {'grade_level': int(match[1]), 'letter': match[2]} if match else None


def same_class_label(left, right):
    first, second = class_fields(left), class_fields(right)
    return first == second if first is not None and second is not None else (left or '').strip() == (right or '').strip()
