/**
 * One visit, in full (F9.AC14, DESIGN §10.3) -- shared by the visit page and the drawer on the
 * Visits list (E5).
 *
 * Two lists make every verdict explainable:
 *
 * - **The derivation** (F4.AC11): every source's candidate, its weight, and whether it was
 *   accepted or suppressed and why -- plus the sources that said nothing, which the API reports
 *   as `inference.source_absent` signals. Together they cover every source for every visit.
 * - **The fired rules** (F5.AC2): every classification rule with its category, weight and
 *   evidence, so `bot_score` and `spoof_score` can be re-added by hand.
 *
 * Sections, not tabs (DESIGN §10.3): an investigator wants Ctrl-F across everything.
 */

import { Link } from 'react-router';
import { useApi } from '@/api/query';
import { visitDetailSchema, type VisitDetail } from '@/api/schemas';
import {
  Badge,
  Card,
  DataTable,
  EmptyState,
  ErrorNotice,
  Identifier,
  KeyValue,
  Loading,
} from '@/components/ui';
import { countryName, label, pct, when } from '@/format';
import { GroundTruthCard } from '@/components/groundtruth/GroundTruthCard';
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

/** Fetches and renders one visit; loading, error and data states (F9.AC18). */
export function VisitDetailBody({
  visitId,
  zone,
}: {
  readonly visitId: string;
  readonly zone: string;
}): React.JSX.Element {
  const query = useApi(`/api/v1/visits/${encodeURIComponent(visitId)}`, null, visitDetailSchema);
  if (query.isPending) return <Loading kind="table" label="Loading the visit…" />;
  if (query.isError) return <ErrorNotice error={query.error.error} />;
  return <Detail visit={query.data} zone={zone} />;
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
    <div className="visit-detail">
      <ul className="summary-cells" aria-label="Summary">
        <li>
          <span className="t-meta">Location</span>
          <strong>{place.text}</strong>
          <span className="t-meta">
            {place.confidence === null
              ? 'No source placed it'
              : `${place.confirmed ? 'Confirmed' : 'Best guess'}, ${pct(place.confidence)} confidence`}
          </span>
        </li>
        <li>
          <span className="t-meta">Class</span>
          <strong>
            <Badge dot={visit.classification}>{label(visit.classification)}</Badge>
          </strong>
          <span className="t-meta">
            bot {visit.bot_score ?? '—'} · spoof {visit.spoof_score ?? '—'}
            {visit.honeypot_tripped ? ' · honeypot tripped' : ''}
          </span>
        </li>
        <li>
          <span className="t-meta">Device</span>
          <strong>
            {visit.device.browser ?? 'Unknown browser'} · {visit.device.os ?? 'unknown OS'}
          </strong>
          <span className="t-meta">
            {label(visit.device.class)}
            {visit.device.webview_host === null ? '' : ` · in-app: ${visit.device.webview_host}`}
          </span>
        </li>
        <li>
          <span className="t-meta">Network</span>
          <strong>{visit.network.asn_org ?? 'Unknown network'}</strong>
          <span className="t-meta">
            {visit.network.asn === null ? 'unknown ASN' : `AS${String(visit.network.asn)}`} ·{' '}
            {label(visit.network.connection_class)}
          </span>
        </li>
      </ul>

      <Card title="About this visit" headingLevel={2}>
        <KeyValue
          items={[
            { key: 'when', label: 'When', value: when(visit.occurred_at, zone, true) },
            {
              key: 'link',
              label: 'Link',
              value: `${visit.link.slug} — ${visit.link.label}`,
            },
            {
              key: 'stage',
              label: 'Stage',
              value: `${label(visit.stage)} · consent ${label(visit.consent_state)}`,
            },
            {
              key: 'source',
              label: 'Located by',
              value:
                visit.location.primary_source === null ? '—' : label(visit.location.primary_source),
            },
            {
              key: 'versions',
              label: 'Versions',
              value: (
                <span className="t-mono">
                  {visit.inference_version ?? 'not inferred'} ·{' '}
                  {visit.classifier_version ?? 'not classified'}
                </span>
              ),
            },
            {
              key: 'trace',
              label: 'Trace',
              value:
                visit.trace_id === null ? (
                  '—'
                ) : (
                  <Identifier value={visit.trace_id} label="Copy trace id" full />
                ),
            },
            {
              key: 'visitor',
              label: 'Visitor',
              value:
                visit.visitor_id === null ? (
                  'No visitor ID (server-only visit)'
                ) : (
                  <Link className="link" to={`/visitors/${visit.visitor_id}`}>
                    {visit.is_returning === true ? 'Returning' : 'First visit'} — every visit by
                    this visitor
                  </Link>
                ),
            },
          ]}
        />
      </Card>

      <Card
        title="Location, per level"
        description="The best guess is the highest-confidence value at each level. Confirmed levels passed the strict threshold; only they drive geofencing."
      >
        <DataTable
          caption="Location per level"
          compact
          rowKey={(level) => level}
          rows={LEVELS}
          columns={[
            { key: 'level', header: 'Level', render: (level) => label(level) },
            {
              key: 'guess',
              header: 'Best guess',
              render: (level) => {
                const value =
                  visit.location.advisory[level === 'country' ? 'country_code' : level] ?? null;
                return value === null ? '—' : level === 'country' ? countryName(value) : value;
              },
            },
            {
              key: 'conf',
              header: 'Confidence',
              numeric: true,
              render: (level) => pct(visit.location.confidence[level] ?? null),
            },
            {
              key: 'confirmed',
              header: 'Confirmed',
              render: (level) =>
                (visit.location.strict[level === 'country' ? 'country_code' : level] ?? null) ===
                null ? (
                  <span className="muted">No</span>
                ) : (
                  <Badge tone="ok">Yes</Badge>
                ),
            },
            {
              key: 'why',
              header: 'Why not confirmed',
              render: (level) => {
                const strict =
                  visit.location.strict[level === 'country' ? 'country_code' : level] ?? null;
                const reason = visit.location.abstain_reason[level];
                return strict === null && typeof reason === 'string' ? label(reason) : '—';
              },
            },
          ]}
        />
      </Card>

      <GroundTruthCard visit={visit} />

      <Card
        title="Derivation: every candidate"
        description="Each source's proposal, its weight, and whether consensus accepted it (F4.AC11)."
      >
        {visit.inferred_at === null ? (
          <EmptyState
            title="Not inferred yet"
            reason="This visit is still in the inference queue."
          />
        ) : visit.candidates.length === 0 ? (
          <EmptyState
            title="No source proposed a candidate"
            reason="The reasons each source gave are listed below."
          />
        ) : (
          <CandidatesTable candidates={visit.candidates} />
        )}
        {absent.length > 0 && (
          <div className="subsection">
            <h3 className="t-section">Sources that said nothing</h3>
            <DataTable
              caption="Sources without a candidate"
              compact
              rowKey={(_, i) => String(i)}
              rows={absent}
              columns={[
                {
                  key: 'source',
                  header: 'Source',
                  render: (s) => label(text((s.detail as Record<string, unknown> | null)?.source)),
                },
                {
                  key: 'status',
                  header: 'Status',
                  render: (s) => text((s.detail as Record<string, unknown> | null)?.status),
                },
                {
                  key: 'reason',
                  header: 'Reason',
                  render: (s) => text((s.detail as Record<string, unknown> | null)?.reason),
                },
              ]}
            />
          </div>
        )}
      </Card>

      <Card
        title="Classification: every fired rule"
        description="Each rule with its category, weight and evidence (F5.AC2)."
      >
        {scoring.length === 0 ? (
          <EmptyState title="No classification rule fired" />
        ) : (
          <>
            <DataTable
              caption="Fired rules"
              compact
              rowKey={(_, i) => String(i)}
              rows={scoring}
              columns={[
                {
                  key: 'rule',
                  header: 'Rule',
                  render: (s) => <span className="t-mono">{s.rule_id}</span>,
                },
                { key: 'cat', header: 'Category', render: (s) => label(s.category) },
                { key: 'w', header: 'Weight', numeric: true, render: (s) => s.weight },
                {
                  key: 'evidence',
                  header: 'Evidence',
                  className: 'evidence',
                  render: (s) => <span className="t-mono">{evidence(s.detail)}</span>,
                },
              ]}
            />
            <p className="t-meta subsection">
              Bot weights sum to {sum('bot')} and spoof weights to {sum('spoof')}; each score is
              that sum, capped at 100. Network rules carry no weight: they decide the class or
              record a fact.
            </p>
          </>
        )}
        {reasons.length > 0 && (
          <div className="subsection">
            <h3 className="t-section">Recorded absences</h3>
            <DataTable
              caption="Absences"
              compact
              rowKey={(_, i) => String(i)}
              rows={reasons}
              columns={[
                {
                  key: 'reason',
                  header: 'Reason',
                  render: (s) => <span className="t-mono">{s.rule_id}</span>,
                },
                {
                  key: 'detail',
                  header: 'Detail',
                  className: 'evidence',
                  render: (s) => <span className="t-mono">{evidence(s.detail)}</span>,
                },
              ]}
            />
          </div>
        )}
      </Card>

      <details className="ui-card raw-fields">
        <summary className="t-section">Raw device and request fields</summary>
        <pre className="raw">
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

/** Every candidate with its weight and outcome (F4.AC11); also the labelling queue's left side. */
export function CandidatesTable({
  candidates,
}: {
  readonly candidates: VisitDetail['candidates'];
}): React.JSX.Element {
  return (
    <DataTable
      caption="Candidates"
      compact
      rowKey={(_, i) => String(i)}
      rows={candidates}
      columns={[
        { key: 'source', header: 'Source', render: (c) => label(c.source) },
        { key: 'level', header: 'Level', render: (c) => label(c.level) },
        {
          key: 'proposed',
          header: 'Proposed',
          render: (c) =>
            [c.city, c.admin2, c.admin1, c.country_code].filter((x) => x !== null).join(', ') ||
            '—',
        },
        {
          key: 'raw',
          header: 'Raw',
          numeric: true,
          render: (c) => c.raw_confidence.toFixed(2),
        },
        { key: 'w', header: 'Weight', numeric: true, render: (c) => c.weight.toFixed(2) },
        {
          key: 'eff',
          header: 'Effective',
          numeric: true,
          render: (c) => c.effective_weight.toFixed(2),
        },
        {
          key: 'outcome',
          header: 'Outcome',
          render: (c) =>
            c.accepted ? (
              <Badge tone="ok">Accepted</Badge>
            ) : (
              <Badge tone="warn">{`Suppressed: ${label(c.suppressed_reason ?? 'unknown')}`}</Badge>
            ),
        },
        {
          key: 'evidence',
          header: 'Evidence',
          className: 'evidence',
          render: (c) => <span className="t-mono">{evidence(c.evidence)}</span>,
        },
        { key: 'ms', header: 'ms', numeric: true, render: (c) => c.latency_ms },
      ]}
    />
  );
}
