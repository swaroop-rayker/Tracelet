/**
 * One visit, in full (F9.AC14).
 *
 * Two lists make every verdict explainable:
 *
 * - **The derivation** (F4.AC11): every source's candidate, its weight, and whether it
 *   was accepted or suppressed and why -- plus the sources that said nothing, which the
 *   API reports as `inference.source_absent` signals. Together they cover every source
 *   for every visit.
 * - **The fired rules** (F5.AC2): every classification rule with its category, weight
 *   and evidence, so `bot_score` and `spoof_score` can be re-added by hand.
 */

import { Link, useParams } from 'react-router';
import { useApi } from '@/api/query';
import { visitDetailSchema, type VisitDetail } from '@/api/schemas';
import { TableView } from '@/components/EChart';
import { Panel } from '@/components/Panel';
import { countryName, label, pct, when } from '@/format';
import { useSession } from '@/session';
import { placeOf } from '@/visits';

const LEVELS = ['country', 'admin1', 'admin2', 'city'] as const;

interface Signal {
  readonly rule_id: string;
  readonly category: string;
  readonly weight: number;
  readonly detail: unknown;
}

function asSignal(raw: Record<string, unknown>): Signal {
  return {
    rule_id: typeof raw.rule_id === 'string' ? raw.rule_id : 'unknown',
    category: typeof raw.category === 'string' ? raw.category : 'unknown',
    weight: typeof raw.weight === 'number' ? raw.weight : 0,
    detail: raw.detail ?? null,
  };
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—';
}

/** Evidence objects are small JSON; shown compactly, never interpreted. */
function evidence(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object' && Object.keys(value).length === 0) return '—';
  return JSON.stringify(value);
}

export default function VisitDetailPage(): React.JSX.Element {
  const { visitId = '' } = useParams();
  const { me } = useSession();
  const query = useApi(`/api/v1/visits/${encodeURIComponent(visitId)}`, null, visitDetailSchema);
  return (
    <div className="page">
      <p>
        <Link to={{ pathname: '/visits', search: window.location.search }}>← Back to visits</Link>
      </p>
      <Panel query={query} title="Visit" isEmpty={() => false} empty={null}>
        {(visit) => <Detail visit={visit} zone={me.timezone} />}
      </Panel>
    </div>
  );
}

function Detail({
  visit,
  zone,
}: {
  readonly visit: VisitDetail;
  readonly zone: string;
}): React.JSX.Element {
  const signals = visit.signals.map(asSignal);
  const scoring = signals.filter(
    (s) => s.category !== 'absence' && !s.rule_id.startsWith('inference.'),
  );
  const absent = signals.filter((s) => s.rule_id === 'inference.source_absent');
  const reasons = signals.filter(
    (s) => s.category === 'absence' && s.rule_id !== 'inference.source_absent',
  );
  const place = placeOf(visit);
  const sum = (category: string): number =>
    scoring.filter((s) => s.category === category).reduce((n, s) => n + s.weight, 0);

  return (
    <div className="stack">
      <dl className="pairs">
        <dt>When</dt>
        <dd>{when(visit.occurred_at, zone, true)}</dd>
        <dt>Link</dt>
        <dd>
          {visit.link.slug} — {visit.link.label}
        </dd>
        <dt>Classification</dt>
        <dd>
          <span className={`badge ${visit.classification}`}>{label(visit.classification)}</span> ·
          bot {visit.bot_score ?? '—'} · spoof {visit.spoof_score ?? '—'}
          {visit.honeypot_tripped ? ' · honeypot tripped' : ''}
        </dd>
        <dt>Location</dt>
        <dd>
          {place.text}
          {visit.location.primary_source === null
            ? ''
            : ` · from ${label(visit.location.primary_source)}`}
        </dd>
        <dt>Stage</dt>
        <dd>
          {label(visit.stage)} · consent {label(visit.consent_state)}
        </dd>
        <dt>Versions</dt>
        <dd className="mono">
          {visit.inference_version ?? 'not inferred'} ·{' '}
          {visit.classifier_version ?? 'not classified'}
        </dd>
        <dt>Trace</dt>
        <dd className="mono">{visit.trace_id ?? '—'}</dd>
        <dt>Visitor</dt>
        <dd>
          {visit.visitor_id === null ? (
            'No visitor ID'
          ) : (
            <Link to={`/visitors/${visit.visitor_id}`}>
              {visit.is_returning === true ? 'Returning' : 'First visit'} — every visit by this
              visitor
            </Link>
          )}
        </dd>
      </dl>

      <section aria-labelledby="levels-h">
        <h4 id="levels-h">Strict and advisory, per level</h4>
        <TableView
          caption="Location per level"
          table={{
            columns: ['Level', 'Strict', 'Advisory', 'Confidence', 'Why strict abstained'],
            rows: LEVELS.map((level) => {
              const key = level === 'country' ? 'country_code' : level;
              const strict = visit.location.strict[key] ?? null;
              const advisory = visit.location.advisory[key] ?? null;
              const reason = visit.location.abstain_reason[level];
              return [
                label(level),
                strict === null ? 'Abstained' : level === 'country' ? countryName(strict) : strict,
                advisory === null ? '—' : level === 'country' ? countryName(advisory) : advisory,
                pct(visit.location.confidence[level] ?? null),
                strict === null && typeof reason === 'string' ? label(reason) : '—',
              ];
            }),
          }}
        />
      </section>

      <section aria-labelledby="derivation-h">
        <h4 id="derivation-h">Derivation: every candidate</h4>
        {visit.inferred_at === null ? (
          <p className="muted">Not inferred yet: this visit is still in the inference queue.</p>
        ) : visit.candidates.length === 0 ? (
          <p className="muted">No source proposed a candidate. The reasons are listed below.</p>
        ) : (
          <TableView
            caption="Candidates"
            table={{
              columns: [
                'Source',
                'Level',
                'Proposed',
                'Raw',
                'Weight',
                'Effective',
                'Outcome',
                'Evidence',
                'ms',
              ],
              rows: visit.candidates.map((c) => [
                label(c.source),
                label(c.level),
                [c.city, c.admin2, c.admin1, c.country_code].filter((x) => x !== null).join(', ') ||
                  '—',
                c.raw_confidence.toFixed(2),
                c.weight.toFixed(2),
                c.effective_weight.toFixed(2),
                c.accepted ? 'Accepted' : `Suppressed: ${label(c.suppressed_reason ?? 'unknown')}`,
                evidence(c.evidence),
                c.latency_ms,
              ]),
            }}
          />
        )}
        {absent.length > 0 && (
          <>
            <h5>Sources that said nothing</h5>
            <TableView
              caption="Sources without a candidate"
              table={{
                columns: ['Source', 'Status', 'Reason'],
                rows: absent.map((s) => {
                  const d = (s.detail ?? {}) as Record<string, unknown>;
                  return [label(text(d.source)), text(d.status), text(d.reason)];
                }),
              }}
            />
          </>
        )}
      </section>

      <section aria-labelledby="rules-h">
        <h4 id="rules-h">Classification: every fired rule</h4>
        {scoring.length === 0 ? (
          <p className="muted">No classification rule fired.</p>
        ) : (
          <>
            <TableView
              caption="Fired rules"
              table={{
                columns: ['Rule', 'Category', 'Weight', 'Evidence'],
                rows: scoring.map((s) => [s.rule_id, s.category, s.weight, evidence(s.detail)]),
              }}
            />
            <p className="muted small">
              Bot weights sum to {sum('bot')} and spoof weights to {sum('spoof')}; each score is
              that sum, capped at 100. Network rules carry no weight: they decide the class or
              record a fact.
            </p>
          </>
        )}
        {reasons.length > 0 && (
          <>
            <h5>Recorded absences</h5>
            <TableView
              caption="Absences"
              table={{
                columns: ['Reason', 'Detail'],
                rows: reasons.map((s) => [s.rule_id, evidence(s.detail)]),
              }}
            />
          </>
        )}
      </section>

      <details>
        <summary>Raw device and request fields</summary>
        <pre className="mono raw">
          {JSON.stringify(
            {
              client: visit.client,
              request: visit.request,
              referer: visit.referer,
              utm: visit.utm,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
