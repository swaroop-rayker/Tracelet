"""Unit tests run with no ambient configuration.

``api-tools`` receives the real ``.env``, so without this a unit test asserting
"an unset optional secret raises" passes in CI and fails on the developer's
machine -- or, worse, the reverse. A unit test must depend on nothing but its own
inputs, so every ``TRACELET_*`` variable is removed for the duration.

Scoped to ``tests/unit`` deliberately: the integration suite needs
``TRACELET_DATABASE_URL`` to find the real database it must not mock (ES3).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tracelet.config import get_settings


@pytest.fixture(autouse=True)
def _no_ambient_tracelet_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in [name for name in os.environ if name.startswith("TRACELET_")]:
        monkeypatch.delenv(key, raising=False)
    # Settings is cached process-wide; a cached instance built from the ambient
    # environment would defeat the deletions above.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
