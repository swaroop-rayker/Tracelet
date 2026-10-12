# ---------------------------------------------------------------------------
# Tracelet live check (M9): security headers and CSP against a deployed URL, in
# Chromium, Firefox and WebKit. Tools only: run by `./scripts/tl live-check <url>`
# from a workstation, never on the production box (ARCHITECTURE section 8).
#
# The official image ships the browsers but not the Python package; it is pinned to
# the same version so the package drives the browsers it was built with.
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.63.0
WORKDIR /qa
