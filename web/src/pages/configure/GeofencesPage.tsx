/**
 * Geofences (DESIGN §16, F6): every boundary that decides which visits raise a high-priority
 * alert, with the import, export and coordinate-test tools beside the list.
 *
 * Reads are open to every admin. Writes are the owner's (CLAUDE.md invariant 9): an analyst
 * sees "New geofence" and "Import" disabled with the reason (UI-17), and the active state as
 * text rather than a switch. The server refuses them regardless.
 *
 * Turning a geofence on or off is optimistic (UI-13: reversible and idempotent); an import is
 * preview → confirm → result (UI-15), because it writes many rows at once.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import type { ApiError } from '@/api/client';
import {
  deleteGeofence,
  exportGeofences,
  geofencesSchema,
  importGeofences,
  testCoordinate,
  updateGeofence,
  type CoordinateTest,
  type Geofence,
} from '@/api/geofences';
import { useApi } from '@/api/query';
import { linkChoicesSchema } from '@/api/schemas';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  Badge,
  Button,
  DataTable,
  Dialog,
  ErrorNotice,
  Field,
  Glyph,
  Menu,
  MenuItem,
  Popover,
  Submit,
  Switch,
  toast,
} from '@/components/ui';
import { count } from '@/format';
import {
  PRIORITY,
  SHAPE,
  STATE,
  regionLabel,
  toned,
  undeterminedReason,
} from '@/pages/configure/geofence-format';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

const FENCES = '/api/v1/geofences';
const OWNER_ONLY = 'Only the owner can change geofences.';

export default function GeofencesPage(): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const navigate = useNavigate();
  const query = useApi(FENCES, null, geofencesSchema);
  const links = useApi('/api/v1/links', null, linkChoicesSchema);
  const labels = new Map((links.data ?? []).map((l) => [l.id, l.label]));
  const client = useQueryClient();
  const [deleting, setDeleting] = useState<Geofence | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<ApiError | null>(null);

  async function remove(fence: Geofence): Promise<void> {
    setDeleteBusy(true);
    setDeleteError(null);
    const result = await deleteGeofence(me.csrf_token, fence.id);
    setDeleteBusy(false);
    if (!result.ok) {
      setDeleteError(result.error);
      return;
    }
    setDeleting(null);
    toast(`${fence.name} deleted.`);
    client.removeQueries({ queryKey: [`${FENCES}/${encodeURIComponent(fence.id)}`] });
    void client.invalidateQueries({ queryKey: [FENCES] });
  }

  return (
    <div className="page">
      <PageHeader
        title="Geofences"
        description="Boundaries that decide which visits raise a high-priority alert."
        actions={
          <Button
            variant="primary"
            icon="Add"
            disabledReason={owner ? null : OWNER_ONLY}
            onClick={() => {
              void navigate('/geofences/new');
            }}
          >
            New geofence
          </Button>
        }
      />
      <div className="toolbar">
        <ExportButton />
        <ImportButton owner={owner} />
        <CoordinateTest />
      </div>
      <Panel
        query={query}
        kind="table"
        title="Geofences"
        description="Highest priority first: where geofences overlap, the highest decides the alert. Matches count visits inside each one in the last 7 days."
        isEmpty={(d) => d.length === 0}
        empty="No geofences yet. Every visit's alert is then a normal one, with no geofence line."
      >
        {(fences) => (
          <DataTable
            caption="Geofences"
            rowKey={(g) => g.id}
            rows={fences}
            columns={[
              {
                key: 'name',
                header: 'Name',
                render: (g) => (
                  <span className="link-cell">
                    <Link className="link" to={`/geofences/${g.id}`}>
                      {g.name}
                    </Link>
                    {g.unknown_region_keys.length > 0 && (
                      <Badge tone="warn">
                        {g.unknown_region_keys.length === 1
                          ? '1 unknown region'
                          : `${String(g.unknown_region_keys.length)} unknown regions`}
                      </Badge>
                    )}
                  </span>
                ),
              },
              {
                key: 'shape',
                header: 'Shape',
                render: (g) => (
                  <span className="glyph-label">
                    <Glyph name={SHAPE[g.shape_kind].icon} />
                    {SHAPE[g.shape_kind].label}
                    {g.shape_kind === 'region' && g.region_keys !== null && (
                      <span className="t-meta" title={g.region_keys.map(regionLabel).join('; ')}>
                        ({count(g.region_keys.length)})
                      </span>
                    )}
                  </span>
                ),
              },
              {
                key: 'scope',
                header: 'Scope',
                render: (g) =>
                  g.link_ids === null
                    ? 'All links'
                    : g.link_ids.map((id) => labels.get(id) ?? 'Removed link').join(', '),
              },
              {
                key: 'priority',
                header: 'Priority',
                numeric: true,
                render: (g) => count(g.priority),
              },
              {
                key: 'alert',
                header: 'Alert',
                render: (g) => (
                  <Badge {...toned(PRIORITY[g.notify_priority].tone)}>
                    {PRIORITY[g.notify_priority].label}
                  </Badge>
                ),
              },
              {
                key: 'active',
                header: 'Active',
                render: (g) => <ActiveCell fence={g} owner={owner} />,
              },
              {
                key: 'matches',
                header: 'Matches 7d',
                numeric: true,
                render: (g) => (g.is_active || g.matches_7d > 0 ? count(g.matches_7d) : '—'),
              },
              {
                key: 'actions',
                header: <span className="sr-only">Actions</span>,
                render: (g) => (
                  <Menu label={`Actions for ${g.name}`} icon="More" iconOnly size="sm" align="end">
                    {(close) => (
                      <>
                        <MenuItem
                          icon="ToolEdit"
                          onSelect={() => {
                            close();
                            void navigate(`/geofences/${g.id}`);
                          }}
                        >
                          {owner ? 'Edit' : 'Open'}
                        </MenuItem>
                        <MenuItem
                          icon="Delete"
                          danger
                          disabled={!owner}
                          {...(owner ? {} : { hint: 'Owner only' })}
                          onSelect={() => {
                            close();
                            setDeleteError(null);
                            setDeleting(g);
                          }}
                        >
                          Delete permanently…
                        </MenuItem>
                      </>
                    )}
                  </Menu>
                ),
              },
            ]}
          />
        )}
      </Panel>
      <ConfirmDialog
        open={deleting !== null}
        title={deleting === null ? '' : `Delete ${deleting.name} permanently?`}
        confirmLabel="Delete permanently"
        busyLabel="Deleting…"
        danger
        typed={
          deleting === null
            ? undefined
            : { label: `Type ${deleting.name} to confirm`, phrase: deleting.name }
        }
        busy={deleteBusy}
        error={deleteError}
        onClose={() => {
          setDeleting(null);
        }}
        onConfirm={() => {
          if (deleting !== null) void remove(deleting);
        }}
      >
        It is removed from the database, not archived, and visits are no longer evaluated against
        it. Visits it already matched keep the match, and the deletion is recorded in the audit log.
        This cannot be undone; an export first keeps a copy.
      </ConfirmDialog>
    </div>
  );
}

/** Optimistic and reversible (UI-13): the switch moves at once and moves back on failure. */
function ActiveCell({
  fence,
  owner,
}: {
  readonly fence: Geofence;
  readonly owner: boolean;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [value, setValue] = useState(fence.is_active);
  if (!owner) return <span>{fence.is_active ? 'On' : 'Off'}</span>;
  return (
    <Switch
      label={`${fence.name} active`}
      hideLabel
      checked={value}
      onChange={(next) => {
        setValue(next);
        void updateGeofence(me.csrf_token, fence.id, { is_active: next }).then((result) => {
          if (!result.ok) {
            setValue(!next);
            toast(
              `Could not ${next ? 'activate' : 'deactivate'} ${fence.name}: ${result.error.message}`,
            );
            return;
          }
          void client.invalidateQueries({ queryKey: [FENCES] });
        });
      }}
    />
  );
}

function ExportButton(): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  return (
    <>
      <Button
        icon="Download"
        busy={busy}
        busyLabel="Exporting…"
        onClick={() => {
          setBusy(true);
          setError(null);
          void exportGeofences().then((result) => {
            setBusy(false);
            if (!result.ok) {
              setError(result.error);
              return;
            }
            const url = URL.createObjectURL(
              new Blob([result.data], { type: 'application/geo+json' }),
            );
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = 'geofences.geojson';
            anchor.click();
            URL.revokeObjectURL(url);
          });
        }}
      >
        Export GeoJSON
      </Button>
      {error !== null && <ErrorNotice error={error} />}
    </>
  );
}

/** `value[name]` of untyped JSON, or undefined. */
function member(value: unknown, name: string): unknown {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)[name]
    : undefined;
}

interface Preview {
  readonly collection: unknown;
  readonly names: readonly string[];
}

/** Read a GeoJSON file, show what it holds, then import it whole (UI-15, F6.AC9). */
function ImportButton({ owner }: { readonly owner: boolean }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function read(file: File): Promise<void> {
    setReadError(null);
    setError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(await file.text());
    } catch {
      setReadError(`${file.name} is not JSON.`);
      return;
    }
    const features = member(parsed, 'features');
    if (!Array.isArray(features)) {
      setReadError(`${file.name} is not a GeoJSON FeatureCollection.`);
      return;
    }
    const names = features.map((f: unknown, i) => {
      const name = member(member(f, 'properties'), 'name');
      return typeof name === 'string' ? name : `Feature ${String(i + 1)} (no name)`;
    });
    setPreview({ collection: parsed, names });
  }

  async function submit(): Promise<void> {
    if (preview === null) return;
    setBusy(true);
    setError(null);
    const result = await importGeofences(me.csrf_token, preview.collection);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPreview(null);
    toast(
      result.data.length === 1
        ? 'Imported 1 geofence.'
        : `Imported ${String(result.data.length)} geofences.`,
    );
    void client.invalidateQueries({ queryKey: [FENCES] });
  }

  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".geojson,.json,application/geo+json,application/json"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file !== undefined) void read(file);
        }}
      />
      <Button
        icon="Upload"
        disabledReason={owner ? null : OWNER_ONLY}
        onClick={() => input.current?.click()}
      >
        Import GeoJSON…
      </Button>
      {readError !== null && <p className="error-text small">{readError}</p>}
      <Dialog
        open={preview !== null}
        size="md"
        title="Import geofences"
        dismissible={!busy}
        onClose={() => {
          setPreview(null);
          setError(null);
        }}
      >
        {preview !== null && (
          <div className="stack">
            <p className="t-secondary">
              {preview.names.length === 1
                ? 'This file holds 1 geofence.'
                : `This file holds ${String(preview.names.length)} geofences.`}{' '}
              Each is checked as a new geofence would be. If any is invalid, none is imported.
            </p>
            <ul className="plain import-list">
              {preview.names.map((name, i) => (
                <li key={`${String(i)}-${name}`}>{name}</li>
              ))}
            </ul>
            {error !== null && (
              <div className="stack-sm">
                <ErrorNotice error={error} />
                {error.fields.length > 0 && (
                  <ul className="plain small">
                    {error.fields.map((f) => (
                      <li key={`${f.field}-${f.code}`}>
                        <span className="t-mono">{f.field}</span>: {f.message}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            <div className="dialog__actions">
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setPreview(null);
                  setError(null);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                busy={busy}
                busyLabel="Importing…"
                onClick={() => {
                  void submit();
                }}
              >
                Import {count(preview.names.length)}
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </>
  );
}

/** F6.AC10: evaluate a coordinate without creating a visit (DESIGN §16). */
function CoordinateTest(): React.JSX.Element {
  const { me } = useSession();
  const [lat, setLat] = useState('');
  const [lng, setLng] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<CoordinateTest | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  async function run(): Promise<void> {
    const point = { lat: Number(lat), lng: Number(lng) };
    if (
      lat.trim() === '' ||
      lng.trim() === '' ||
      !Number.isFinite(point.lat) ||
      !Number.isFinite(point.lng) ||
      Math.abs(point.lat) > 90 ||
      Math.abs(point.lng) > 180
    ) {
      setInvalid('Latitude −90 to 90, longitude −180 to 180, in decimal degrees.');
      return;
    }
    setInvalid(null);
    setBusy(true);
    setError(null);
    const response = await testCoordinate(me.csrf_token, point);
    setBusy(false);
    if (!response.ok) {
      setError(response.error);
      return;
    }
    setResult(response.data);
  }

  return (
    <Popover
      label="Test a coordinate"
      align="start"
      className="coordinate-test"
      trigger={(props) => (
        <Button icon="Locate" {...props}>
          Test a coordinate
        </Button>
      )}
    >
      {() => (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
        >
          <p className="t-secondary small">
            Treated as a consented GPS fix: the point itself, and the state and country GeoNames
            names for it. No visit is created.
          </p>
          <div className="coordinate-test__fields">
            <Field label="Latitude" inputMode="numeric" value={lat} onChange={setLat} mono />
            <Field label="Longitude" inputMode="numeric" value={lng} onChange={setLng} mono />
          </div>
          {invalid !== null && <p className="error-text small">{invalid}</p>}
          <Submit busy={busy} busyLabel="Testing…">
            Test
          </Submit>
          {error !== null && <ErrorNotice error={error} />}
          {result !== null && <TestResult result={result} />}
        </form>
      )}
    </Popover>
  );
}

function TestResult({ result }: { readonly result: CoordinateTest }): React.JSX.Element {
  const placed = [result.placed.admin1, result.placed.country_code].filter((p) => p !== null);
  return (
    <div className="stack-sm" role="status">
      <p className="small">
        {placed.length > 0 ? `Placed in ${placed.join(', ')}.` : 'GeoNames could not place it.'}{' '}
        {result.state === null
          ? 'No active geofence to test against.'
          : `Overall: ${STATE[result.state].label.toLowerCase()}.`}
      </p>
      {result.results.length > 0 && (
        <ul className="plain coordinate-test__results">
          {result.results.map((r) => (
            <li key={r.geofence_id}>
              <span>{r.name}</span>
              <Badge {...toned(STATE[r.result].tone)}>{STATE[r.result].label}</Badge>
              {r.result === 'undetermined' && (
                <span className="t-meta">{undeterminedReason(r.reason)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
