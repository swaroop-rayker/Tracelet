# ---------------------------------------------------------------------------
# Tracelet — Makefile
#
# Every target delegates to scripts/tl, which is the single implementation.
# This exists so `make verify` works where make is installed (WSL, CI, Linux);
# `./scripts/tl verify` is identical and works in Git Bash on Windows, where
# make is not present on a stock host (SPEC C5).
#
# Do not add logic here. Add it to scripts/tl.
# ---------------------------------------------------------------------------

TL := ./scripts/tl

.PHONY: help bootstrap up down verify migrate fmt openapi logs ps psql shell db-setup

help:       ; @$(TL) help
bootstrap:  ; @$(TL) bootstrap
up:         ; @$(TL) up
down:       ; @$(TL) down
verify:     ; @$(TL) verify
migrate:    ; @$(TL) migrate
fmt:        ; @$(TL) fmt
openapi:    ; @$(TL) openapi
logs:       ; @$(TL) logs
ps:         ; @$(TL) ps
psql:       ; @$(TL) psql
shell:      ; @$(TL) shell
db-setup:   ; @$(TL) db-setup
