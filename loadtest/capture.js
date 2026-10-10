// Tracelet load test (ADR-0027, NFR1, NFR2, F14.AC2). Run from a workstation, never on the
// production box:  ./scripts/tl loadtest https://<site> <slug> [full|limits]
//
//   full    10 cold visitors back to back for 15 minutes (NFR1.AC1, sustained), then 30
//           for 3 minutes (NFR1.AC5's 3x headroom: slower or shed is a pass, an error is
//           not). The owner raises the capture limits for this window (F11.AC9).
//   limits  At the default limits, after the owner restores them: 60 visits in a minute
//           from one network. The limiter must engage (F11.AC2) and every visitor must
//           still be sent on (F11.AC3, CLAUDE.md invariant 1).
//
// Every visit is cold (no cookie) and sends an honest automation user agent that the
// classifier lists, so no visit is "human" and none can alert (CLAUDE.md invariant 6).
// The test link points at a fixed destination; the page that sends the visitor there is
// the capture page (or a direct redirect), and either must name the destination.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE;
const SLUG = __ENV.SLUG;
const DESTINATION = __ENV.DESTINATION || "https://example.com";
const MODE = __ENV.MODE || "full";
const UA = "Go-http-client/1.1 (Tracelet load test; k6)";

const full = {
  sustained: { executor: "constant-vus", vus: 10, duration: "15m" },
  headroom: { executor: "constant-vus", vus: 30, duration: "3m", startTime: "15m30s" },
};
const limits = {
  limits: { executor: "constant-arrival-rate", rate: 60, timeUnit: "1m", duration: "1m",
            preAllocatedVUs: 5, maxVUs: 10 },
};

export const options = {
  scenarios: MODE === "limits" ? limits : full,
  thresholds: {
    // NFR1.AC1: no error. A visitor who is not sent on is the failure that matters.
    checks: ["rate==1"],
    http_req_failed: ["rate==0"],
    // NFR2.AC2, direct-origin path: time to first byte about 800-1000 ms from India.
    "http_req_waiting{scenario:sustained}": ["p(95)<1000"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  userAgent: UA,
  insecureSkipTLSVerify: false,
};

export default function () {
  http.cookieJar().clear(BASE);
  const res = http.get(`${BASE}/r/${SLUG}`, { redirects: 0, tags: { name: "capture" } });
  check(res, {
    "visitor is sent on": (r) =>
      (r.status === 200 && r.body.includes(DESTINATION)) ||
      ((r.status === 302 || r.status === 303 || r.status === 307) &&
        (r.headers.Location || "").startsWith(DESTINATION)),
  });
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
  text += `failed ${(m.http_req_failed.values.rate * 100).toFixed(2)}%  `;
  text += `checks ${(m.checks.values.rate * 100).toFixed(2)}%\n`;
  for (const [name, key] of [
    ["ttfb sustained", "http_req_waiting{scenario:sustained}"],
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
