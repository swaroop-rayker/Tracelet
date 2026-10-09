/**
 * Ground truth (DESIGN §16 M8, §12 E37; F4.AC13, F4.AC15, F4.AC17, F9.AC10; ADR-0024).
 *
 * How often the engine was right about visits whose true location an owner knows. Every
 * figure is a replay of the consensus over the labelled visits, shown with its sample and
 * its 95 % interval beside it (RISKS R9): 31 of 31 is not certainty. Population, settings
 * version and tab live in the URL (UI-9).
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router';
import type { ApiError } from '@/api/client';
import {
  GT,
  population as populationEnum,
  recordRun,
  refreshAccuracy,
  reportSchema,
  type Population,
} from '@/api/groundtruth';
import { useApi } from '@/api/query';
import { HEALTH, inferenceSchema } from '@/api/system';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  Button,
  Dialog,
  ErrorNotice,
  Field,
  SegmentedControl,
  Select,
  Tabs,
  toast,
} from '@/components/ui';
import { useSession } from '@/session';
import { AccuracyLevels } from './AccuracyLevels';
import { QueueTab } from './QueueTab';
import { CoverageTab, LabelsTab, RunsTab, SourcesTab } from './tabs';

const TABS = [
  { value: 'queue', label: 'Queue' },
  { value: 'labels', label: 'Labels' },
  { value: 'sources', label: 'Sources' },
  { value: 'coverage', label: 'Coverage' },
  { value: 'runs', label: 'Runs' },
] as const;
type Tab = (typeof TABS)[number]['value'];

const POPULATION_LABEL: Readonly<Record<Population, string>> = {
  network_only: 'Network only',
  consented: 'Consented',
  non_consented: 'Not consented',
  all: 'All',
  vpn: 'VPN',
};

const POPULATION_HINT: Readonly<Record<Population, string>> = {
  network_only:
    'Every label without a VPN, scored on what the network alone says: a consented visit’s GPS is set aside. F4.AC13’s targets are gated here.',
  consented: 'Visits whose visitor allowed location, scored with their GPS.',
  non_consented: 'Visits without consent, as recorded.',
  all: 'Every label as recorded, GPS included where it was given.',
  vpn: 'Visits labelled VPN on. The address is the VPN’s, so the right answer is to confirm nothing: any confirmed country or state here fails (SPEC §11 row 32).',
};

function asTab(raw: string | null): Tab {
  return TABS.find((t) => t.value === raw)?.value ?? 'queue';
}

function asPopulation(raw: string | null): Population {
  const parsed = populationEnum.safeParse(raw);
  return parsed.success ? parsed.data : 'network_only';
}

export default function GroundTruthPage(): React.JSX.Element {
  const [search, setSearch] = useSearchParams();
  const tab = asTab(search.get('tab'));
  const population = asPopulation(search.get('population'));
  const versionParam = search.get('settings_version');
  const version = versionParam !== null && /^\d+$/.test(versionParam) ? versionParam : null;
  const report = useApi(
    `${GT}/metrics`,
    version === null ? null : new URLSearchParams({ settings_version: version }),
    reportSchema,
  );
  const engine = useApi(`${HEALTH}/inference`, null, inferenceSchema);

  function set(key: string, value: string | null): void {
    const next = new URLSearchParams(search);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setSearch(next, { replace: true });
  }

  const active = engine.data?.active_version ?? null;
  const versions =
    active === null
      ? []
      : Array.from({ length: Math.max(active, Number(version ?? 0)) }, (_, i) => i + 1).reverse();

  return (
    <div className="page">
      <PageHeader
        title="Ground truth"
        description="How often the engine was right about visits whose true location you know."
        actions={<RecordButton />}
      />
      <div className="toolbar">
        <SegmentedControl
          label="Population"
          value={population}
          onChange={(value) => {
            set('population', value === 'network_only' ? null : value);
          }}
          options={(Object.keys(POPULATION_LABEL) as Population[]).map((p) => ({
            value: p,
            label: POPULATION_LABEL[p],
          }))}
        />
        {versions.length > 0 && (
          <Select
            label="Settings version"
            size="sm"
            value={version ?? String(active)}
            onChange={(value) => {
              set('settings_version', value === String(active) ? null : value);
            }}
            options={versions.map((v) => ({
              value: String(v),
              label:
                v === active ? `Scored under v${String(v)} · active` : `Scored under v${String(v)}`,
            }))}
          />
        )}
      </div>

      <Panel
        query={report}
        title="Accuracy per level"
        description={POPULATION_HINT[population]}
        kind="kpi"
        isEmpty={(r) => r.label_count === 0}
        empty={(r) => (
          <>
            No labelled visit has been scored yet
            {r.cant_tell > 0 ? ` (${String(r.cant_tell)} recorded as can’t tell)` : ''}
            {r.pending > 0 ? `, and ${String(r.pending)} still wait for inference` : ''}. Label a
            visit in the queue below: open one of your links on a network you know, then label that
            visit.
          </>
        )}
      >
        {(data) => (
          <AccuracyLevels
            report={data}
            population={population}
            notActive={version !== null && Number(version) !== active}
          />
        )}
      </Panel>

      <Tabs
        label="Ground truth views"
        tabs={TABS}
        value={tab}
        onChange={(value) => {
          set('tab', value === 'queue' ? null : value);
        }}
      />
      <div role="tabpanel" aria-label={TABS.find((t) => t.value === tab)?.label}>
        {tab === 'queue' && <QueueTab search={search} set={set} />}
        {tab === 'labels' && <LabelsTab />}
        {tab === 'sources' && <SourcesTab report={report} />}
        {tab === 'coverage' && <CoverageTab report={report} />}
        {tab === 'runs' && <RunsTab />}
      </div>
    </div>
  );
}

function RecordButton(): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function record(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await recordRun(me.csrf_token, note.trim() === '' ? null : note.trim());
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(`Recorded: ${result.data.inference_version}, ${String(result.data.label_count)} labels.`);
    refreshAccuracy(client, null);
    setOpen(false);
    setNote('');
  }

  return (
    <>
      <Button
        icon="GroundTruth"
        disabled={!owner}
        disabledReason={owner ? null : 'Only an owner can record a measurement.'}
        onClick={() => {
          setOpen(true);
        }}
      >
        Record this measurement
      </Button>
      {open && (
        <Dialog
          open
          size="sm"
          title="Record this measurement"
          dismissible={!busy}
          onClose={() => {
            setOpen(false);
          }}
          footer={
            <>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setOpen(false);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                busy={busy}
                busyLabel="Recording…"
                onClick={() => {
                  void record();
                }}
              >
                Record
              </Button>
            </>
          }
        >
          <div className="stack">
            <p className="m-0">
              Scores the active settings version against every label now and keeps the result in
              Runs. Runs are history: they are never changed or deleted.
            </p>
            <Field
              label="Note (optional)"
              value={note}
              onChange={setNote}
              maxLength={200}
              required={false}
            />
            {error !== null && <ErrorNotice error={error} />}
          </div>
        </Dialog>
      )}
    </>
  );
}
