/**
 * System health › Overview (DESIGN §16 M7; F10.AC1, AC2, AC6, AC14, AC15).
 *
 * What is degraded, with what still works; the host's figures with their thresholds in
 * words as well as colour; readiness, taken over from Settings › System; and the links to
 * the things F10 says are managed from here (links, admins, the delivery log). Everything
 * polls every 15 s while the tab is visible and says how fresh it is (UI-18).
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { fetchReadiness, type ApiResult, type Readiness } from '@/api/health';
import { useApi } from '@/api/query';
import {
  HEALTH,
  degradationSchema,
  systemSchema,
  type Condition,
  type SystemSample,
  type Usage,
} from '@/api/system';
import { Panel } from '@/components/Panel';
import { Freshness } from '@/components/shell/Freshness';
import { Alert, Badge, Card, KeyValue, Stat, Stats } from '@/components/ui';
import { pct } from '@/format';
import { bytes, duration, severityTone, stateTone } from '@/pages/health/health-format';
import { useSession } from '@/session';

/** Where each condition is put right. */
const FIX_AT: Readonly<Record<string, { readonly to: string; readonly label: string }>> = {
  backups: { to: '/health/data', label: 'Backups' },
  restore: { to: '/health/data', label: 'Backups' },
  download: { to: '/health/data', label: 'Backups' },
  disk: { to: '/health/data', label: 'Retention' },
  outbox: { to: '/alerts?status=dead', label: 'Dead letters' },
};

function fixAt(key: string): { readonly to: string; readonly label: string } | null {
  if (key.startsWith('geodb:')) return { to: '/health/databases', label: 'Geo databases' };
  return FIX_AT[key] ?? null;
}

export default function OverviewPage(): React.JSX.Element {
  return (
    <>
      <Conditions />
      <HostFigures />
      <ReadinessCard />
      <Card title="Also managed from here" headingLevel={2}>
        <ul className="health-links">
          <li>
            <Link to="/links">Tracking links</Link> — destinations, defaults, archive (F10.AC6)
          </li>
          <li>
            <Link to="/settings/team">Team</Link> — invite, change role, disable admins (F10.AC10)
          </li>
          <li>
            <Link to="/alerts">Alerts</Link> — Telegram, quiet hours, the delivery log (F10.AC13)
          </li>
        </ul>
      </Card>
    </>
  );
}

function Conditions(): React.JSX.Element {
  const { me } = useSession();
  const query = useApi(`${HEALTH}/degradation`, null, degradationSchema, {
    refetchInterval: me.health_refresh_seconds * 1000,
  });
  return (
    <Panel
      query={query}
      kind="text"
      title="What is degraded"
      description="Each condition says what still works. The redirect never waits on any of them."
      actions={<Freshness iso={query.data?.checked_at} zone={me.timezone} />}
      isEmpty={(d) => d.conditions.length === 0}
      empty={<p className="muted">Nothing is degraded.</p>}
    >
      {(d) => (
        <div className="stack">
          {d.conditions.map((c) => (
            <ConditionAlert key={c.key} condition={c} />
          ))}
        </div>
      )}
    </Panel>
  );
}

function ConditionAlert({ condition }: { readonly condition: Condition }): React.JSX.Element {
  const fix = fixAt(condition.key);
  return (
    <Alert
      tone={severityTone(condition.severity)}
      title={condition.title}
      action={fix ? <Link to={fix.to}>{fix.label} ›</Link> : undefined}
    >
      <p>{condition.detail}</p>
      <p className="small muted">Still works: {condition.still_works}</p>
    </Alert>
  );
}

function usageNote(u: Usage, unit: (n: number) => string): React.JSX.Element {
  const tone = stateTone(u.state);
  return (
    <>
      {unit(u.used)} of {unit(u.total)}
      {tone !== 'ok' && (
        <>
          {' '}
          <Badge tone={tone}>{tone === 'error' ? 'Critical' : 'Warning'}</Badge>{' '}
          <span>above the {String(u.warn_percent)} % threshold</span>
        </>
      )}
    </>
  );
}

function HostFigures(): React.JSX.Element {
  const { me } = useSession();
  // F10.AC1: the admin's own interval, from Settings › Preferences.
  const query = useApi(`${HEALTH}/system`, null, systemSchema, {
    refetchInterval: me.health_refresh_seconds * 1000,
  });
  return (
    <Panel
      query={query}
      kind="kpi"
      title="Host"
      description="The VM the stack runs on, read from its own /proc and /sys (F10.AC15)."
      isEmpty={() => false}
      empty={null}
    >
      {(s) => <HostBody s={s} />}
    </Panel>
  );
}

function HostBody({ s }: { readonly s: SystemSample }): React.JSX.Element {
  const [load1, load5, load15] = s.cpu.load;
  return (
    <div className="stack">
      {s.scope !== 'host' && (
        <Alert tone="warn" title="These figures are the container's">
          {s.scope_reason}
        </Alert>
      )}
      <Stats label="Host figures">
        <Stat
          label="CPU"
          value={pct(s.cpu.percent / 100)}
          note={`${String(s.cpu.count)} CPU${s.cpu.count === 1 ? '' : 's'}`}
        />
        <Stat
          label="Memory"
          value={pct(s.memory.percent / 100)}
          note={usageNote(s.memory, bytes)}
        />
        <Stat
          label="Swap"
          value={s.swap.total === 0 ? '—' : pct(s.swap.percent / 100)}
          note={s.swap.total === 0 ? 'No swap on this host' : usageNote(s.swap, bytes)}
        />
        <Stat label="Disk" value={pct(s.disk.percent / 100)} note={usageNote(s.disk, bytes)} />
      </Stats>
      <KeyValue
        items={[
          {
            key: 'load',
            label: 'Load',
            value: `${load1.toFixed(2)} · ${load5.toFixed(2)} · ${load15.toFixed(2)} (1, 5, 15 min)`,
          },
          { key: 'uptime', label: 'Uptime', value: duration(s.uptime_seconds) },
          { key: 'database', label: 'Database', value: bytes(s.database_bytes) },
          {
            key: 'temperature',
            label: 'Temperature',
            value:
              s.temperature.celsius === null
                ? `— ${s.temperature.reason ?? ''}`
                : `${s.temperature.celsius.toFixed(1)} °C (${s.temperature.sensor ?? 'sensor'})`,
          },
        ]}
      />
    </div>
  );
}

/** `/readyz` is a cheap liveness probe, not a host metric: a fixed minute is enough. */
const READINESS_MS = 60_000;

function ReadinessCard(): React.JSX.Element {
  const [result, setResult] = useState<ApiResult<Readiness> | null>(null);
  useEffect(() => {
    let live = true;
    const check = async (): Promise<void> => {
      const r = await fetchReadiness();
      if (live) setResult(r);
    };
    void check();
    const timer = window.setInterval(() => void check(), READINESS_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);
  return (
    <Card
      title="Readiness"
      description="What the server checks before it reports itself ready."
      headingLevel={2}
    >
      {result === null && <p className="muted">Checking…</p>}
      {result?.kind === 'failure' && (
        <Alert tone="error" title="Readiness could not be checked">
          {result.message}
        </Alert>
      )}
      {result?.kind === 'success' && (
        <ul className="health-checks">
          {Object.entries(result.data.checks).map(([name, check]) => (
            <li key={name}>
              <Badge tone={check.ok ? 'ok' : 'error'}>{check.ok ? 'OK' : 'Failing'}</Badge>{' '}
              <strong>{name}</strong> <span className="muted">{check.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
