// Tracelet load test (ADR-0027, NFR1, NFR2, F14.AC2, SPEC section 11 row 35). Run from a
// workstation, never on the production box:
//   ./scripts/tl loadtest https://<site> <slug> [burst|stress|limits]
//
//   burst   NFR1.AC1 as clarified (row 35): 10 simultaneous cold visitors, one burst every
//           10 s for 15 minutes (~86 000 a day). Must meet NFR2: no error, TTFB p95 < 1 s
//           on the direct-origin path. The owner raises the capture limits for the window.
//   stress  NFR1.AC5: 10 visitors back to back for 15 minutes, then 30 for 3. Passes on
//           CLAUDE.md invariant 1 alone: every visitor sent on, however slowly, telemetry
//           degrading visibly (ADR-0028). Raised limits too.
//   limits  At the default limits, after the owner restores them: 60 visits in a minute
//           from one network. The limiter must engage (F11.AC2) and every visitor must
//           still be sent on (F11.AC3).
//
// Every visit is cold (no cookie) and sends an honest automation user agent the classifier
// lists, so no visit is "human" and none can alert (CLAUDE.md invariant 6).
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE;
const SLUG = __ENV.SLUG;
const DESTINATION = __ENV.DESTINATION || "https://example.com";
const MODE = __ENV.MODE || "burst";
const UA = "Go-http-client/1.1 (Tracelet load test; k6)";

const SCENARIOS = {
  burst: {
    burst: {
      executor: "constant-arrival-rate",
      rate: 1,
      timeUnit: "10s",
      duration: "15m",
      preAllocatedVUs: 3,
      maxVUs: 10,
    },
  },
  stress: {
    sustained: { executor: "constant-vus", vus: 10, duration: "15m" },
    headroom: { executor: "constant-vus", vus: 30, duration: "3m", startTime: "15m30s" },
  },
  limits: {
    limits: { executor: "constant-arrival-rate", rate: 60, timeUnit: "1m", duration: "1m",
              preAllocatedVUs: 5, maxVUs: 10 },
  },
};

const THRESHOLDS = {
  // Every mode: no visitor left behind. A 503 that still carries the destination (the
  // fallback page, F15.AC7) is sent on; a 504 from Caddy is not.
  burst: {
    checks: ["rate==1"],
    http_req_failed: ["rate==0"],
    "http_req_waiting{scenario:burst}": ["p(95)<1000"],
  },
  stress: { checks: ["rate==1"] },
  limits: { checks: ["rate==1"] },
};

export const options = {
  scenarios: SCENARIOS[MODE],
  thresholds: THRESHOLDS[MODE],
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  userAgent: UA,
};

// In burst mode only 200 and the immediate redirects count as success for
// http_req_failed; the check below is what decides "sent on" in every mode.
http.setResponseCallback(http.expectedStatuses(200, 302, 303, 307));

function sentOn(r) {
  if ((r.status === 200 || r.status === 503) && r.body && r.body.includes(DESTINATION)) {
    return true;
  }
  return [302, 303, 307].includes(r.status) && (r.headers.Location || "").startsWith(DESTINATION);
}

function one() {
  http.cookieJar().clear(BASE);
  return { method: "GET", url: `${BASE}/r/${SLUG}`, params: { redirects: 0, tags: { name: "capture" } } };
}

export default function () {
  const responses = MODE === "burst"
    ? http.batch(Array.from({ length: 10 }, one))
    : [http.get(`${BASE}/r/${SLUG}`, { redirects: 0, tags: { name: "capture" } })];
  for (const r of responses) {
    check(r, { "visitor is sent on": sentOn });
  }
}

function line(name, metric) {
  if (!metric) return `${name}: none\n`;
  const v = metric.values;
  const ms = (x) => (x === undefined ? "-" : `${x.toFixed(0)}ms`);
  return `${name}: med ${ms(v.med)}  p95 ${ms(v["p(95)"])}  p99 ${ms(v["p(99)"])}  max ${ms(v.max)}\n`;
}

export function handleSummary(data) {
  const m = data.metrics;
  let text = `\nmode ${MODE}  requests ${m.http_reqs.values.count}  `;
  text += `rate ${m.http_reqs.values.rate.toFixed(1)}/s  `;
  text += `non-2xx/3xx ${(m.http_req_failed.values.rate * 100).toFixed(2)}%  `;
  text += `sent on ${(m.checks.values.rate * 100).toFixed(2)}%\n`;
  for (const [name, key] of [
    ["ttfb burst", "http_req_waiting{scenario:burst}"],
    ["ttfb all", "http_req_waiting"],
    ["total all", "http_req_duration"],
  ]) {
    text += line(name, m[key]);
  }
  for (const [name, t] of Object.entries(data.metrics)) {
    if (t.thresholds) {
      for (const [expr, res] of Object.entries(t.thresholds)) {
        text += `threshold ${name} ${expr}: ${res.ok ? "ok" : "BREACHED"}\n`;
      }
    }
  }
  return { [`/scripts/out/summary-${MODE}.json`]: JSON.stringify(data, null, 2), stdout: text };
}
