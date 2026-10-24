import re
from datetime import datetime

_NONDIGIT_RE = re.compile(r'\D')

def isoparse(iso_str):
    """Parses the limited subset of `ISO8601-formatted time`_ strings as
    returned by :meth:`datetime.datetime.isoformat`.

    >>> epoch_dt = datetime.fromtimestamp(0, timezone.utc).replace(tzinfo=None)
    >>> iso_str = epoch_dt.isoformat()
    >>> print(iso_str)
    1970-01-01T00:00:00
    >>> isoparse(iso_str)
    datetime.datetime(1970, 1, 1, 0, 0)

    >>> utcnow = datetime.now(timezone.utc).replace(tzinfo=None)
    >>> utcnow == isoparse(utcnow.isoformat())
    True

    For further datetime parsing, see the `iso8601`_ package for strict
    ISO parsing and `dateutil`_ package for loose parsing and more.

    .. _ISO8601-formatted time: https://en.wikipedia.org/wiki/ISO_8601
    .. _iso8601: https://pypi.python.org/pypi/iso8601
    .. _dateutil: https://pypi.python.org/pypi/python-dateutil

    """
    dt_args = [int(p) for p in _NONDIGIT_RE.split(iso_str)]
    return datetime(*dt_args)
