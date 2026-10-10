"""The geo readers run on maxminddb's C extension (docs/ERRORS.md E79).

``MODE_AUTO`` prefers the C extension and silently falls back to the pure-Python reader,
which costs about nine times the CPU per lookup. On the e2-micro that fallback turned the
``asn_profiles`` rebuild into a 15-minute timeout that starved the workers. A wheel or
platform change that drops the extension must fail here, not on the production box.
"""

from __future__ import annotations

import importlib.util

import maxminddb


def test_the_maxminddb_c_extension_is_installed() -> None:
    assert importlib.util.find_spec("maxminddb.extension") is not None


def test_mode_auto_will_choose_the_c_reader() -> None:
    # open_database(MODE_AUTO) builds the C Reader exactly when this module-level handle
    # loaded (maxminddb/__init__.py); None is the silent pure-Python fallback.
    assert maxminddb._extension is not None
