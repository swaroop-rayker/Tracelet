# Load test results — M9, the e2-micro

**Method:** ADR-0027 (amended). k6 1.3.0 in a container on the owner's PC in India, behind
the PC's TLS-inspecting antivirus (client-side figures are therefore an upper bound),
against `https://tracelet.duckdns.org` (the free-subdomain path, direct to a GCP e2-micro in
`us-central1-a`; SPEC §11 row 33). Every visit is cold and sends
`Go-http-client/1.1 (Tracelet load test; k6)`, so none is `human` and none can alert. Server
figures come from the api's own `http_request` log events (`loadtest/server_timings.py`);
memory from `loadtest/vm_sample.sh` every 5 s. The capture limits were raised by the owner
for `burst` and `stress` and restored before `limits` (F11.AC9, audited).

**Modes** (`./scripts/tl loadtest <url> <slug> [burst|stress|limits]`, SPEC §11 row 35):
`burst` is NFR1.AC1's pass test (10 simultaneous visitors every 10 s for 15 minutes, the
owner working the dashboard on a PC and a phone: NFR1.AC3); `stress` is NFR1.AC5's flood
(10 back to back for 15 minutes, then 30 for 3), which passes on CLAUDE.md invariant 1 alone;
`limits` is 60 visits in a minute at the default limits.

## Final results — release `203f4773aeee`, 2026-10-10/11

| Measure | Result | Target (SPEC, row 37) | |
|---|---|---|---|
| Burst: visitors sent on, errors | 910 of 910, 0 | all, none | ✅ |
| Burst: time to first byte from India, p95 | 1 163 ms (median 616) | about 1.2 s, direct path | ✅ |
| Burst: capture server time, p50 / p95 | 83 / 475 ms | p95 < 500 ms within a burst | ✅ |
| Lone capture, server time p50 / p95 (20 visits, 4 s apart) | 12.5 / 23.6 ms | p95 < 50 ms | ✅ |
| Burst: dashboard API, p95 (two admins) | 857 ms | < 900 ms | ✅ |
| Burst: host memory average / peak / minimum available | 722 / 752 / 217 MB | ≤ 760 MB in use, ≥ 200 MB available | ✅ |
| Burst: workers killed | 0 | 0 | ✅ |
| Stress: visitors sent on | 11 799 of 11 799 (about 1 820 by Caddy's fallback, ADR-0029, not recorded) | all | ✅ |
| Stress: throughput, worst wait | 10.6 req/s, 5.7 s | degrade, never fail | ✅ |
| Stress: host memory average | 692 MB | — | |
| Limits: limiter engaged, visitors sent on | 22 of 60 rate-limited, 60 of 60 sent on | F11.AC2, F11.AC3 | ✅ |

The CPU is the binding constraint: about 0.8 of a vCPU is accounted under load
(ARCHITECTURE §6.5), so ten simultaneous visitors queue behind one another. The original
NFR2 targets (50 ms within a burst, 300 ms for the dashboard) and NFR6.AC1's 700 MB were
not met on this box and were restated to the measured figures by the owner (SPEC §11
row 37).

## How it got there

| Run | Release | Change since the previous run | Stranded visitors | Notes |
|---|---|---|---|---|
| 1, flood | `f94a6e9` | — | **500 of ~6 100 (Caddy 504)** | capture p50 758 ms, worst 54 s; 4 workers killed; memory 771 MB average (ERRORS E82) |
| 2, burst | `b219bfe` | ADR-0028: answered from memory when overloaded, 1 s deadline, jobs paused | 0 of 910 | TTFB p95 860 ms; memory 792 MB |
| 2, flood | `b219bfe` | | 718 of 15 340 | 9 workers killed, stalled on swap |
| 3, flood | `0dbb1b5` + pool 20 | no swap for api and caddy; one worker; OS-config agent off | 956 of 11 421 | memory fixed (682 MB); CPU saturated |
| 4, flood | `203f477` | ADR-0029: Caddy serves a prepared redirect page when the api cannot answer; 1 s dial | **0 of 11 799** | |
| 4, burst | `203f477` | | **0 of 910** | the final results above |

## Housekeeping

The test link `qa-load-test` (destination `https://example.com`) and its visits are deleted
by the owner after the runs. The `ACCURACY_FIXTURE` is unaffected (no labels on load-test
visits).
