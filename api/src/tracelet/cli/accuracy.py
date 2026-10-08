"""``tracelet accuracy ...`` -- ground truth and accuracy from the host (docs/API.md section 11).

Shell access to the host is owner-level already, the standing ``tracelet inference
reset-defaults`` relies on; every label written here is audited as the CLI's, with no actor
(CLAUDE.md invariant 9).

``check`` is different from the rest: it reads a fixture file and **no database**, so CI
can run it with nothing but the package installed (F14.AC12, ADR-0024). It exits 1 when a
gated F4.AC13 target is missed, 2 when the fixture cannot be read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from tracelet.accuracy import fixture, store
from tracelet.accuracy import labels as label_service
from tracelet.accuracy.metrics import Proportion, Report, score
from tracelet.accuracy.models import CONNECTION_KINDS, NETWORKS, GroundTruthLabel
from tracelet.capture.models import Visit
from tracelet.classify.config import CLASSIFIER_REVISION
from tracelet.config import get_settings
from tracelet.db.engine import dispose_engine, init_engine, session_scope
from tracelet.geofence import regions
from tracelet.inference.config import DEFAULT_CONFIG, ENGINE_REVISION, InferenceConfig
from tracelet.logging import configure_logging

_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def register(sub: Any) -> None:
    parser = sub.add_parser("accuracy", help="ground-truth labels and accuracy (M8)")
    commands = parser.add_subparsers(dest="accuracy_command", metavar="<accuracy command>")

    label = commands.add_parser("label", help="label a visit with where it really was")
    label.add_argument("visit_id")
    place = label.add_mutually_exclusive_group(required=True)
    place.add_argument("--country", help="two-letter ISO code, e.g. IN")
    place.add_argument("--cant-tell", action="store_true", help="you do not know where it was")
    label.add_argument("--admin1", help="state, as GeoNames spells it (e.g. Karnataka)")
    label.add_argument("--admin2", help="district")
    label.add_argument("--city", help="city or town")
    label.add_argument("--use-gps", action="store_true", help="copy the visit's own GPS fix")
    label.add_argument("--connection", choices=CONNECTION_KINDS)
    vpn = label.add_mutually_exclusive_group()
    vpn.add_argument("--vpn", dest="vpn", action="store_true", default=None)
    vpn.add_argument("--no-vpn", dest="vpn", action="store_false")
    label.add_argument("--network", choices=NETWORKS, help="the network underneath any VPN")
    label.add_argument("--notes")

    unlabel = commands.add_parser("unlabel", help="delete a visit's label")
    unlabel.add_argument("visit_id")

    commands.add_parser("labels", help="list every label")

    queue = commands.add_parser("queue", help="visits worth labelling")
    queue.add_argument("--order", choices=("conflict", "recent"), default="conflict")
    queue.add_argument("--limit", type=int, default=20)

    report = commands.add_parser("report", help="score the labels (replay; saves nothing)")
    which = report.add_mutually_exclusive_group()
    which.add_argument("--settings-version", type=int, help="a retained version (default: active)")
    which.add_argument("--config-file", type=Path, help="a proposed settings JSON, not saved")
    report.add_argument("--json", action="store_true", help="the full report as JSON")

    run = commands.add_parser("run", help="score the active version and record a run")
    run.add_argument("--note")
    run.add_argument("--git-sha", help="default: $GITHUB_SHA when set")

    export = commands.add_parser("export", help="write the anonymised fixture (ADR-0024)")
    export.add_argument("--out", type=Path, required=True, help="end in .gz to gzip")

    check = commands.add_parser("check", help="CI gate: replay a fixture, no database")
    check.add_argument("--fixture", type=Path, required=True)
    check.add_argument("--config-file", type=Path, help="score under these settings instead")


def _run(coro: Coroutine[Any, Any, int]) -> int:
    async def wrapper() -> int:
        configure_logging(level="WARNING", json_output=False)
        init_engine(get_settings())
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


_CLI = label_service.Actor(admin_id=None, detail={"via": "cli"})


def _visit_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        print(f"{raw!r} is not a visit id.", file=sys.stderr)
        return None


async def _label(args: argparse.Namespace) -> int:
    visit_id = _visit_id(args.visit_id)
    if visit_id is None:
        return 2
    values = label_service.LabelValues(
        cant_tell=bool(args.cant_tell),
        country_code=args.country,
        admin1=args.admin1,
        admin2=args.admin2,
        city=args.city,
        use_gps=True if args.use_gps else None,
        connection_kind=args.connection,
        vpn_used=args.vpn,
        network=args.network,
        notes=args.notes,
    )
    catalog = regions.catalog(get_settings())
    try:
        async with session_scope() as db:
            held = await store.label_of_visit(db, visit_id)
            if held is None:
                label = await label_service.create(
                    db, visit_id, values, catalog=catalog, actor=_CLI
                )
                verb = "Labelled"
            else:
                label = await label_service.update(db, held, values, catalog=catalog, actor=_CLI)
                verb = "Relabelled"
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not trace back
        print(f"Not saved: {exc}", file=sys.stderr)
        for error in getattr(exc, "errors", []):
            print(f"  {error.field}: {error.message}", file=sys.stderr)
        return 1
    truth = (
        "can't tell"
        if label.cant_tell
        else ", ".join(
            p
            for p in (
                label.true_city,
                label.true_admin2,
                label.true_admin1,
                label.true_country_code,
            )
            if p
        )
    )
    print(f"{verb} visit {visit_id}: {truth}.")
    return 0


async def _unlabel(raw: str) -> int:
    visit_id = _visit_id(raw)
    if visit_id is None:
        return 2
    async with session_scope() as db:
        held = await store.label_of_visit(db, visit_id)
        if held is None:
            print(f"Visit {visit_id} has no label.", file=sys.stderr)
            return 1
        await label_service.delete(db, held, actor=_CLI)
    print(f"Deleted the label on visit {visit_id}.")
    return 0


async def _labels() -> int:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(GroundTruthLabel).order_by(GroundTruthLabel.labeled_at.desc())
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        print("No labels yet.")
        return 0
    for r in rows:
        truth = (
            "can't tell"
            if r.cant_tell
            else ", ".join(
                p for p in (r.true_city, r.true_admin2, r.true_admin1, r.true_country_code) if p
            )
        )
        how = " ".join(
            str(p) for p in (r.network, r.connection_kind, "vpn" if r.vpn_used else None) if p
        )
        print(f"{r.labeled_at:%Y-%m-%d %H:%M}  {r.visit_id}  {truth}  {how}".rstrip())
    return 0


async def _queue(order: Literal["conflict", "recent"], limit: int) -> int:
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(Visit)
                    .where(*store.queue_clauses())
                    .order_by(*store.queue_order(order))
                    .limit(max(1, min(limit, 100)))
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        print("Nothing left to label.")
        return 0
    for v in rows:
        guess = ", ".join(
            p for p in (v.advisory_city, v.advisory_admin1, v.advisory_country_code) if p
        )
        conflict = f"{float(v.conflict_score):.2f}" if v.conflict_score is not None else "-"
        print(
            f"{v.occurred_at:%Y-%m-%d %H:%M}  {v.id}  conflict {conflict}  {v.asn_org or '?'}  {guess or '?'}"
        )
    return 0


def _figure(p: Proportion) -> str:
    if p.value is None or p.ci95 is None:
        return "not measured (0)"
    return f"{p.value:.1%} ({p.k} of {p.n}; 95 % {p.ci95[0]:.0%}-{p.ci95[1]:.0%})"


def _print(result: Report) -> None:
    print(
        f"Scored under {result.inference_version}: {result.label_count} labels"
        f" ({result.cant_tell} can't tell, {result.pending} not inferred yet)."
    )
    for population in result.populations:
        print(f"\n{population.population} ({population.label_count} labels)")
        for level in population.levels:
            print(
                f"  {level.level.value:<8} precision {_figure(level.strict_precision)}"
                f" | coverage {_figure(level.strict_coverage)}"
                f" | best guess {_figure(level.advisory_accuracy)}"
            )
    print("\nTargets (F4.AC13)")
    for t in result.targets:
        target = f">= {t.target:.1%}" if t.target is not None else "no target yet"
        value = f"{t.value:.1%}" if t.value is not None else "-"
        print(f"  {t.status:<10} {t.id:<36} {value:>7} {target} (n={t.n})")
    verdict = {True: "PASSED", False: "FAILED", None: "NOTHING GATED WAS MEASURABLE"}
    print(f"\n{verdict[result.passed]}")


def _config_file(path: Path) -> InferenceConfig:
    return InferenceConfig.from_stored(json.loads(path.read_text(encoding="utf-8")))


async def _report(version: int | None, config_file: Path | None, as_json: bool) -> int:
    config = _config_file(config_file) if config_file is not None else None
    async with session_scope() as db:
        result = await store.report(db, version=version, config=config)
    if as_json:
        print(result.model_dump_json(indent=1))
    else:
        if config is not None:
            print("A proposed version, replayed only: nothing was saved.")
        _print(result)
    return 0


async def _record(note: str | None, git_sha: str | None) -> int:
    sha = git_sha or os.environ.get("GITHUB_SHA")
    if sha is not None and not _SHA.match(sha):
        print(f"{sha!r} is not a git commit id.", file=sys.stderr)
        return 2
    async with session_scope() as db:
        result = await store.report(db)
        run = await store.record_run(db, result, origin="cli", actor=None, note=note, git_sha=sha)
    _print(result)
    print(f"\nRecorded run {run.id}.")
    return 0


async def _export(out: Path) -> int:
    async with session_scope() as db:
        version, config = await store.settings_for(db, None)
        loaded = await store.load_cases(db)
    out.parent.mkdir(parents=True, exist_ok=True)
    fixture.write(out, fixture.dumps(loaded.cases, settings_version=version, config=config))
    print(
        f"Wrote {len(loaded.cases)} labelled cases under settings v{version} to {out}."
        f" Skipped {loaded.cant_tell} can't-tell and {loaded.pending} not yet inferred."
    )
    print("It holds no id, time, address, ASN number, PTR, coordinate or note (ADR-0024).")
    print("The repository is public: keep it out of git. Update the CI secret with")
    print(f"  base64 -w0 {out} | gh secret set ACCURACY_FIXTURE")
    return 0


def _check(path: Path, config_file: Path | None) -> int:
    try:
        loaded = fixture.read(path)
    except (OSError, fixture.FixtureError) as exc:
        print(f"Cannot read the fixture: {exc}", file=sys.stderr)
        return 2
    if config_file is not None:
        config, version, label = _config_file(config_file), None, "proposed"
    elif loaded.config is not None:
        config, version, label = (
            loaded.config,
            loaded.settings_version,
            f"s{loaded.settings_version}",
        )
    else:
        config, version, label = DEFAULT_CONFIG, None, "default"
    result = score(
        loaded.cases,
        config,
        settings_version=version,
        inference_version=f"{ENGINE_REVISION}+{label}",
        classifier_version=f"{CLASSIFIER_REVISION}+{label}",
    )
    _print(result)
    return 1 if result.passed is False else 0


def dispatch(args: argparse.Namespace) -> int:
    command = getattr(args, "accuracy_command", None)
    if command == "check":
        return _check(args.fixture, args.config_file)
    if command == "label":
        return _run(_label(args))
    if command == "unlabel":
        return _run(_unlabel(args.visit_id))
    if command == "labels":
        return _run(_labels())
    if command == "queue":
        return _run(_queue(args.order, args.limit))
    if command == "report":
        return _run(_report(args.settings_version, args.config_file, args.json))
    if command == "run":
        return _run(_record(args.note, args.git_sha))
    if command == "export":
        return _run(_export(args.out))
    print("usage: tracelet accuracy {label,unlabel,labels,queue,report,run,export,check} ...")
    return 2
