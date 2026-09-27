"""Shared field types for auth payloads.

**Why not `pydantic.EmailStr`.** It requires `email-validator`, which pulls in
`dnspython`. This system deliberately sends no email at all -- Gate 1 declined an
SMTP provider, and recovery runs over Telegram (ADR-0008) -- so an admin address is
purely a **login identifier**. Paying two dependencies for RFC-5322 conformance on a
string we never deliver to does not pass ES5, and on a 1 GB box every import earns
its place.

The constraint below rejects what would actually cause a problem: no ``@``, no dot in
the domain, whitespace, or an absurd length. Anything stricter would only reject
addresses that are unusual rather than wrong.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

# to_lower because the database column is citext: normalising on the way in keeps the
# stored value and any comparison consistent.
AdminEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$",
    ),
]

Password = Annotated[str, StringConstraints(min_length=1, max_length=512)]

# Generous upper bound: a normalised recovery code is 11 characters, but a person may
# paste one with spaces or dashes.
OpaqueToken = Annotated[str, StringConstraints(min_length=1, max_length=256)]
TotpCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=10)]
RecoveryCodeInput = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=6, max_length=32)
]
