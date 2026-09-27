"""Dump the OpenAPI document to stdout.

This is what makes the TypeScript client generated rather than hand-written
(F14.AC9): the schema is exported here, ``openapi-typescript`` turns it into
types, and CI fails if the committed output differs from a fresh generation.
One source of truth for the API contract.

Written to stdout rather than a path so the api container needs no bind mount of
the repository -- ``tl openapi`` redirects it on the host.

Run with:  python -m tracelet.cli.export_openapi > api/openapi.json
"""

from __future__ import annotations

import json
import sys

from tracelet.config import Settings
from tracelet.main import create_app


def main() -> int:
    # Forced to development, because the production app deliberately serves no
    # schema (an unauthenticated schema browser on a system holding visitor
    # telemetry is free reconnaissance). The document itself is identical.
    app = create_app(Settings(env="development"))
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
