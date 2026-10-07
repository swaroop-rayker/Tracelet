/**
 * System health › Inference (DESIGN §16 M7; F10.AC7, F10.AC8).
 *
 * The **source switches**: each source on or off, saved as one new settings version (F4.AC14:
 * a version is never edited, so a rollback stays possible), applying to visits inferred from
 * then on -- no restart. And the **flow diagram**: the pipeline's stages as columns of nodes
 * in the sankey's node style, which sources are on, the suppression rules and the strict
 * thresholds; with a visit (`?visit=`, UI-9), what inference recorded for it.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import {
  HEALTH,
  flowSchema,
  inferenceSchema,
  saveInferenceSettings,
  type Flow,
  type FlowSample,
  type InferenceState,
} from '@/api/system';
import { Panel } from '@/components/Panel';
import {
  Badge,
  Button,
  Dialog,
  ErrorNotice,
  Field,
  SettingRow,
  Switch,
  cx,
  toast,
} from '@/components/ui';
import { OWNER_ONLY, SOURCE_STATUS, words } from '@/pages/health/health-format';
import { toned } from '@/pages/configure/geofence-format';
import { useSession } from '@/session';

const SETTINGS = `${HEALTH}/inference`;
const FLOW = `${HEALTH}/inference/flow`;

export default function InferencePage(): React.JSX.Element {
  return (
    <>
      <Switches />
      <FlowDiagram />
    </>
  );
}

// ---------------------------------------------------------------------------
// Switches
// ---------------------------------------------------------------------------

function Switches(): React.JSX.Element {
  const settings = useApi(SETTINGS, null, inferenceSchema);
  const flow = useApi(FLOW, null, flowSchema);
  return (
    <Panel
      query={settings}
      kind="list"
      title="Sources"
      description="Applies to visits inferred from now on — no restart. Each save is a new settings version, so it can be rolled back."
      isEmpty={() => false}
      empty={null}
    >
      {(state) => <SwitchList state={state} flow={flow.data ?? null} />}
    </Panel>
  );
}

function SwitchList({
  state,
  flow,
}: {
  readonly state: InferenceState;
  readonly flow: Flow | null;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const saved = useMemo(
    () =>
      Object.fromEntries(Object.entries(state.settings.sources).map(([k, v]) => [k, v.enabled])),
    [state],
  );
  const [draft, setDraft] = useState<Record<string, boolean>>(saved);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    setDraft(saved);
  }, [saved]);

  const nodes = flow?.sources ?? [];
  const changed = nodes.filter((n) => draft[n.source] !== saved[n.source]);
  const owner = me.role === 'owner';

  async function save(): Promise<void> {
    setBusy(true);
    const sources = Object.fromEntries(
      Object.entries(state.settings.sources).map(([k, v]) => [
        k,
        { ...v, enabled: draft[k] ?? v.enabled },
      ]),
    );
    const note = changed
      .map((n) => `${draft[n.source] ? 'on' : 'off'}: ${n.code} ${n.label}`)
      .join('; ');
    const result = await saveInferenceSettings(
      me.csrf_token,
      { ...state.settings, sources },
      `Source switches -- ${note}`,
    );
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setConfirming(false);
    setError(null);
    await client.invalidateQueries({ queryKey: [SETTINGS] });
    await client.invalidateQueries({ queryKey: [FLOW] });
    toast(`Saved as ${result.data.inference_version}. New visits use it now.`);
  }

  return (
    <div className="stack">
      <div className="settings-list">
        {nodes.map((n) => (
          <SettingRow
            key={n.source}
            title={
              <>
                <span className="t-mono small">{n.code}</span> {n.label}
              </>
            }
            description={flow?.families.find((f) => f.family === n.family)?.label}
          >
            <Switch
              label={`${n.code} ${n.label}`}
              hideLabel
              checked={draft[n.source] ?? n.enabled}
              disabled={!owner}
              onChange={(on) => {
                setDraft((d) => ({ ...d, [n.source]: on }));
              }}
            />
          </SettingRow>
        ))}
      </div>
      <div className="row-actions">
        <Button
          variant="primary"
          disabled={changed.length === 0}
          disabledReason={owner ? null : OWNER_ONLY}
          onClick={() => {
            setConfirming(true);
          }}
        >
          Save switches
        </Button>
        {changed.length > 0 && (
          <Button
            onClick={() => {
              setDraft(saved);
            }}
          >
            Discard
          </Button>
        )}
        <span className="small muted">In use: {state.inference_version}</span>
      </div>
      {confirming && (
        <Dialog
          open
          size="sm"
          onClose={() => {
            setConfirming(false);
          }}
          title="Save the source switches?"
          footer={
            <>
              <Button
                onClick={() => {
                  setConfirming(false);
                }}
              >
                Cancel
              </Button>
              <Button variant="primary" busy={busy} busyLabel="Saving…" onClick={() => void save()}>
                Save as a new version
              </Button>
            </>
          }
        >
          <ul className="plain">
            {changed.map((n) => (
              <li key={n.source}>
                <strong>{draft[n.source] ? 'On' : 'Off'}</strong>: {n.code} {n.label}
              </li>
            ))}
          </ul>
          <p className="small muted">
            Visits already inferred keep their version; new ones are inferred under this one.
          </p>
          {error !== null && <ErrorNotice error={error} />}
        </Dialog>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The flow diagram
// ---------------------------------------------------------------------------

const STAGE_LABELS: Readonly<Record<string, string>> = {
  capture: 'Capture',
  sources: 'Sources',
  suppression: 'Suppression',
  consensus: 'Consensus',
  // The last three steps hold one node each, so they share a column (DESIGN §16 M7).
  outcome: 'Classification, geofence, alert',
};

const LEVEL_LABELS: Readonly<Record<string, string>> = {
  country: 'Country',
  admin1: 'State',
  admin2: 'District',
  city: 'City',
};

function FlowDiagram(): React.JSX.Element {
  const [params, setParams] = useSearchParams();
  const visit = params.get('visit') ?? '';
  const [typed, setTyped] = useState(visit);
  useEffect(() => {
    setTyped(visit);
  }, [visit]);
  const query = useApi(
    FLOW,
    visit === '' ? null : new URLSearchParams({ sample_visit_id: visit }),
    flowSchema,
  );

  return (
    <Panel
      query={query}
      kind="chart"
      title="How a visit flows"
      description="Each column is a step, left to right. Show a visit to see what happened to it."
      actions={
        <form
          className="flow-picker"
          onSubmit={(e) => {
            e.preventDefault();
            const next = new URLSearchParams(params);
            if (typed.trim() === '') next.delete('visit');
            else next.set('visit', typed.trim());
            setParams(next);
          }}
        >
          <Field
            label="Visit ID"
            value={typed}
            onChange={setTyped}
            required={false}
            mono
            placeholder="01a1123c-…"
          />
          <Button type="submit" size="sm">
            {typed.trim() === '' && visit !== '' ? 'Clear' : 'Show'}
          </Button>
        </form>
      }
      isEmpty={() => false}
      empty={null}
    >
      {(flow) => <Diagram flow={flow} />}
    </Panel>
  );
}

function Diagram({ flow }: { readonly flow: Flow }): React.JSX.Element {
  const sample = flow.sample;
  const outcome = (source: string) => sample?.sources.find((s) => s.source === source);
  return (
    <div className="stack">
      {sample !== null && <SampleLine sample={sample} active={flow.inference_version} />}
      <div className="flow" role="list" aria-label="Inference pipeline, in order">
        <Column stage="capture">
          <Node title="Server capture" note="IP, network, headers — no JavaScript needed" />
        </Column>
        <Column stage="sources">
          {flow.families.map((family) => (
            <div key={family.family} className="flow__group">
              <p className="flow__group-label">{family.label}</p>
              {family.sources.map((name) => {
                const node = flow.sources.find((s) => s.source === name);
                if (node === undefined) return null;
                const result = outcome(name);
                const status = result ? SOURCE_STATUS[result.status] : undefined;
                return (
                  <Node
                    key={name}
                    title={`${node.code} ${node.label}`}
                    off={!node.enabled}
                    tone={status?.tone}
                    badge={
                      result ? (status?.label ?? result.status) : node.enabled ? undefined : 'Off'
                    }
                    note={
                      result
                        ? [
                            result.reason ? words(result.reason) : null,
                            ...result.candidates.map((c) => `${c.level}: ${c.value}`),
                          ]
                            .filter(Boolean)
                            .join(' · ') || undefined
                        : undefined
                    }
                  />
                );
              })}
            </div>
          ))}
        </Column>
        <Column stage="suppression">
          {flow.rules.map((rule) => (
            <Node
              key={rule.rule}
              title={rule.label}
              note={rule.description}
              hit={sample?.rules_fired.includes(rule.rule) ?? false}
            />
          ))}
        </Column>
        <Column stage="consensus">
          {flow.levels.map((level) => {
            const l = sample?.levels.find((x) => x.level === level.level);
            return (
              <Node
                key={level.level}
                title={`${LEVEL_LABELS[level.level] ?? level.level} ≥ ${level.threshold.toFixed(2)}`}
                note={
                  l
                    ? l.strict !== null
                      ? `strict ${l.strict}${l.confidence !== null ? ` (${l.confidence.toFixed(2)})` : ''}`
                      : [
                          l.advisory !== null ? `best guess ${l.advisory}` : null,
                          words(l.abstain_reason) || 'abstained',
                        ]
                          .filter(Boolean)
                          .join(' · ')
                    : undefined
                }
                tone={l ? (l.strict !== null ? 'ok' : undefined) : undefined}
              />
            );
          })}
        </Column>
        <Column stage="outcome">
          <Node
            title="Classification"
            note={sample ? `This visit: ${sample.classification}` : 'Human, bot or spoofed'}
          />
          <Node
            title="Geofence"
            note={
              sample
                ? `This visit: ${sample.geofence_state ?? 'no geofence applies'}`
                : 'Inside, outside or undetermined — on strict fields only'
            }
          />
          <Node
            title="Alert"
            note={
              sample
                ? sample.alert
                  ? `${String(sample.alert.priority)} · ${String(sample.alert.status)}`
                  : 'No alert'
                : 'Telegram, for humans only'
            }
          />
        </Column>
      </div>
    </div>
  );
}

function SampleLine({
  sample,
  active,
}: {
  readonly sample: FlowSample;
  readonly active: string;
}): React.JSX.Element {
  return (
    <p className="small">
      Showing{' '}
      <Link to={`/visits/${sample.visit_id}`} className="t-mono">
        {sample.visit_id.slice(0, 13)}…
      </Link>
      , inferred under <span className="t-mono">{sample.inference_version ?? 'no version'}</span>
      {sample.inference_version !== null && sample.inference_version !== active && (
        <>
          {' '}
          <Badge tone="info">not the active {active}</Badge>
        </>
      )}
      .
    </p>
  );
}

function Column({
  stage,
  children,
}: {
  readonly stage: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flow__column" role="listitem">
      <h3 className="flow__stage">{STAGE_LABELS[stage] ?? stage}</h3>
      <div className="flow__nodes">{children}</div>
    </div>
  );
}

function Node({
  title,
  note,
  badge,
  tone,
  off = false,
  hit = false,
}: {
  readonly title: string;
  readonly note?: string | undefined;
  readonly badge?: string | undefined;
  readonly tone?: 'ok' | 'warn' | 'info' | undefined;
  readonly off?: boolean;
  readonly hit?: boolean;
}): React.JSX.Element {
  return (
    <div className={cx('flow__node', off && 'flow__node--off', hit && 'flow__node--hit')}>
      <p className="flow__title">
        {title}
        {badge !== undefined && (
          <>
            {' '}
            <Badge {...toned(tone)}>{badge}</Badge>
          </>
        )}
        {hit && (
          <>
            {' '}
            <Badge tone="warn">applied</Badge>
          </>
        )}
      </p>
      {note !== undefined && <p className="flow__note">{note}</p>}
    </div>
  );
}
