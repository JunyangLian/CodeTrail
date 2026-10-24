"""Decode UTF-8 tools and native subprocess output without reader-thread errors."""

import locale


def decode_output(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        fallback = locale.getlocale(locale.LC_CTYPE)[1] or "utf-8"
    except ValueError:
        fallback = "utf-8"
    for encoding in dict.fromkeys(("utf-8", fallback)):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("utf-8", errors="replace")
