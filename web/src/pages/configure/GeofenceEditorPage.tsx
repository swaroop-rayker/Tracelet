/**
 * The geofence editor (DESIGN §16, F6.AC1-AC4, ADR-0020): a full-width map of the M5
 * outlines with a tool rail on the left and an inspector on the right.
 *
 * Three shapes. A **region** is picked on the map or from the searchable list, which also
 * holds the divisions the map cannot draw. A **polygon** or **circle** is drawn with Geoman,
 * or typed as coordinates -- there is no street map (ADR-0020 decision 1), so typing is how a
 * campus is drawn precisely. A self-intersecting ring is refused by the server, which says
 * where; the point is marked on the map and the rule is named beside it (F6.AC4).
 *
 * The draft is the single source of truth; the map draws it (GeofenceMap). Only a changed
 * shape is sent on save, so a polygon imported with holes keeps them unless it is redrawn.
 *
 * Every write is the owner's. An analyst can open a geofence and read it, with the tools and
 * Save disabled and the reason given (UI-17).
 */

import { useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import type { ApiError } from '@/api/client';
import {
  createGeofence,
  deleteGeofence,
  geofenceSchema,
  placesSchema,
  regionsSchema,
  updateGeofence,
  type Geofence,
  type GeofenceShape,
  type NotifyPriority,
  type Regions,
  type ShapeKind,
} from '@/api/geofences';
import { useApi } from '@/api/query';
import { linkChoicesSchema, type LinkChoice } from '@/api/schemas';
import type { CircleShape, Ring, Tool } from '@/components/map/GeofenceMap';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  ErrorNotice,
  Field,
  IconButton,
  Input,
  Loading,
  Menu,
  MenuItem,
  SearchInput,
  SegmentedControl,
  Select,
  Switch,
  toast,
} from '@/components/ui';
import { countryName } from '@/format';
import { SHAPE, regionLabel } from '@/pages/configure/geofence-format';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

const GeofenceMap = lazy(() => import('@/components/map/GeofenceMap'));

const FENCES = '/api/v1/geofences';
const OWNER_ONLY = 'Only the owner can change geofences.';
const MIN_RADIUS = 50;
const MAX_RADIUS = 1_000_000;
/** How long typing pauses before a typed shape is applied to the map. */
const TYPING_MS = 500;

interface Draft {
  readonly name: string;
  readonly description: string;
  readonly priority: string;
  readonly notify: NotifyPriority;
  readonly active: boolean;
  readonly links: readonly string[] | null;
  readonly kind: ShapeKind;
  readonly regionKeys: readonly string[];
  readonly ring: Ring | null;
  readonly circle: CircleShape | null;
  /** The shape was changed here, so it is sent on save. */
  readonly shapeDirty: boolean;
}

const EMPTY: Draft = {
  name: '',
  description: '',
  priority: '0',
  notify: 'high',
  active: true,
  links: null,
  kind: 'region',
  regionKeys: [],
  ring: null,
  circle: null,
  shapeDirty: true,
};

function fromGeofence(g: Geofence): Draft {
  const exterior = g.shape_kind === 'polygon' ? (g.geometry?.coordinates[0] ?? []) : [];
  // GeoJSON is [lng, lat] and closed; the draft is [lat, lng] and open.
  const ring = exterior.slice(0, -1).map((p) => [p[1] ?? 0, p[0] ?? 0] as const);
  return {
    name: g.name,
    description: g.description ?? '',
    priority: String(g.priority),
    notify: g.notify_priority,
    active: g.is_active,
    links: g.link_ids,
    kind: g.shape_kind,
    regionKeys: g.region_keys ?? [],
    ring: g.shape_kind === 'polygon' ? ring : null,
    circle:
      g.shape_kind === 'circle' && g.center !== null && g.radius_m !== null
        ? { lat: g.center.lat, lng: g.center.lng, radius_m: g.radius_m }
        : null,
    shapeDirty: false,
  };
}

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

/** What the draft would send as its shape, or why it cannot yet. */
function shapeOf(d: Draft): GeofenceShape | string {
  if (d.kind === 'region') {
    return d.regionKeys.length === 0
      ? 'Pick at least one country or state.'
      : { shape_kind: 'region', region_keys: d.regionKeys };
  }
  if (d.kind === 'circle') {
    if (d.circle === null) return 'Draw the circle, or type its centre and radius.';
    if (d.circle.radius_m < MIN_RADIUS || d.circle.radius_m > MAX_RADIUS) {
      return 'The radius is 50 m to 1,000 km.';
    }
    return {
      shape_kind: 'circle',
      center: { lat: round6(d.circle.lat), lng: round6(d.circle.lng) },
      radius_m: d.circle.radius_m,
    };
  }
  if (d.ring === null || d.ring.length < 3) return 'A polygon needs at least three points.';
  const ring = d.ring.map(([lat, lng]) => [round6(lng), round6(lat)]);
  const first = ring[0];
  return {
    shape_kind: 'polygon',
    geometry: { type: 'Polygon', coordinates: [first === undefined ? ring : [...ring, first]] },
  };
}

export default function GeofenceEditorPage(): React.JSX.Element {
  const { geofenceId } = useParams();
  const isNew = geofenceId === undefined;
  const existing = useApi(
    `${FENCES}/${encodeURIComponent(geofenceId ?? 'new')}`,
    null,
    geofenceSchema,
    { enabled: !isNew },
  );
  const regions = useApi(`${FENCES}/regions`, null, regionsSchema);
  const links = useApi('/api/v1/links', null, linkChoicesSchema);

  if (!isNew && existing.isPending) return <Loading label="Loading the geofence…" />;
  if (!isNew && existing.isError) {
    return (
      <div className="page">
        <PageHeader title="Geofence" />
        <ErrorNotice error={existing.error.error} />
        <p>
          <Link className="link" to="/geofences">
            Back to geofences
          </Link>
        </p>
      </div>
    );
  }
  return (
    <Editor
      key={existing.data?.updated_at ?? 'new'}
      fence={existing.data ?? null}
      regions={regions.data ?? null}
      regionsError={regions.isError ? regions.error.error : null}
      links={links.data ?? []}
    />
  );
}

function Editor({
  fence,
  regions,
  regionsError,
  links,
}: {
  readonly fence: Geofence | null;
  readonly regions: Regions | null;
  readonly regionsError: ApiError | null;
  readonly links: readonly LinkChoice[];
}): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const navigate = useNavigate();
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(fence === null ? EMPTY : fromGeofence(fence));
  const [tool, setTool] = useState<Tool>(fence === null ? 'regions' : 'select');
  // The country opened on the map: a region geofence opens on its own country; anything else
  // starts on the world, where a click opens a country (owner, 2026-10-06).
  const [focus, setFocus] = useState<string | null>(
    () => draft.regionKeys[0]?.split('|')[0] ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<ApiError | null>(null);

  const places = useApi(
    `${FENCES}/places`,
    new URLSearchParams({ country: focus ?? '' }),
    placesSchema,
    { enabled: focus !== null },
  );
  const keyByCode = useMemo(
    () => new Map((regions?.divisions ?? []).map((d) => [d.code, d.key])),
    [regions],
  );
  const holes = fence?.shape_kind === 'polygon' ? (fence.geometry?.coordinates.length ?? 1) - 1 : 0;
  const errorAt = error?.fields.find((f) => f.location !== undefined)?.location ?? null;

  const set = (patch: Partial<Draft>): void => {
    setDraft((d) => ({ ...d, ...patch }));
  };
  const setShape = (patch: Partial<Draft>): void => {
    setError(null);
    setDraft((d) => ({ ...d, ...patch, shapeDirty: true }));
  };
  const choose = (next: Tool): void => {
    if (next === 'regions') setShape({ kind: 'region' });
    if (next === 'polygon') setShape({ kind: 'polygon', ring: null, circle: null });
    if (next === 'circle') setShape({ kind: 'circle', ring: null, circle: null });
    setTool(next);
  };

  async function save(): Promise<void> {
    const shape = shapeOf(draft);
    const priority = Number(draft.priority);
    if (draft.name.trim() === '') {
      setProblem('Give the geofence a name.');
      return;
    }
    if (!Number.isInteger(priority)) {
      setProblem('Priority is a whole number.');
      return;
    }
    if (typeof shape === 'string') {
      setProblem(shape);
      return;
    }
    setProblem(null);
    setBusy(true);
    setError(null);
    const fields = {
      name: draft.name.trim(),
      description: draft.description.trim() === '' ? null : draft.description.trim(),
      priority,
      is_active: draft.active,
      notify_priority: draft.notify,
      link_ids: draft.links === null || draft.links.length === 0 ? null : draft.links,
    };
    const result =
      fence === null
        ? await createGeofence(me.csrf_token, { ...fields, ...shape })
        : await updateGeofence(
            me.csrf_token,
            fence.id,
            draft.shapeDirty ? { ...fields, ...shape } : fields,
          );
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(fence === null ? `${result.data.name} created.` : `${result.data.name} saved.`);
    void client.invalidateQueries({ queryKey: [FENCES] });
    void navigate(`/geofences/${result.data.id}`, { replace: true });
  }

  async function remove(): Promise<void> {
    if (fence === null) return;
    setDeleteBusy(true);
    setDeleteError(null);
    const result = await deleteGeofence(me.csrf_token, fence.id);
    setDeleteBusy(false);
    if (!result.ok) {
      setDeleteError(result.error);
      return;
    }
    toast(`${fence.name} deleted.`);
    void client.invalidateQueries({ queryKey: [FENCES] });
    void navigate('/geofences');
  }

  const tools: readonly {
    readonly tool: Tool;
    readonly label: string;
    readonly icon:
      'ToolSelect' | 'ShapeRegion' | 'ShapePolygon' | 'ShapeCircle' | 'ToolEdit' | 'ToolMove';
  }[] = [
    { tool: 'select', label: 'Select', icon: 'ToolSelect' },
    { tool: 'regions', label: 'Pick regions', icon: 'ShapeRegion' },
    { tool: 'polygon', label: 'Draw polygon', icon: 'ShapePolygon' },
    { tool: 'circle', label: 'Draw circle', icon: 'ShapeCircle' },
    { tool: 'edit', label: 'Edit vertices', icon: 'ToolEdit' },
    { tool: 'move', label: 'Move shape', icon: 'ToolMove' },
  ];
  const hasShape = draft.ring !== null || draft.circle !== null;

  return (
    <div className="page geofence-editor">
      <PageHeader
        title={fence === null ? 'New geofence' : fence.name}
        description={
          fence === null
            ? 'Pick countries and states, or draw a polygon or a circle. Regions match the strict country and state; shapes match a visit’s point.'
            : `${SHAPE[fence.shape_kind].label} geofence.`
        }
        actions={
          <>
            <Link className="btn btn--ghost" to="/geofences">
              Cancel
            </Link>
            <Button
              variant="primary"
              busy={busy}
              busyLabel="Saving…"
              disabledReason={owner ? null : OWNER_ONLY}
              onClick={() => {
                void save();
              }}
            >
              {fence === null ? 'Create geofence' : 'Save'}
            </Button>
            {fence !== null && (
              <Menu label={`More actions for ${fence.name}`} icon="More" iconOnly align="end">
                {(close) => (
                  <MenuItem
                    danger
                    icon="Delete"
                    disabled={!owner}
                    hint={owner ? undefined : OWNER_ONLY}
                    onSelect={() => {
                      close();
                      setDeleteError(null);
                      setDeleting(true);
                    }}
                  >
                    Delete geofence…
                  </MenuItem>
                )}
              </Menu>
            )}
          </>
        }
      />

      <div className="geofence-editor__body">
        <div
          className="tool-rail"
          role="toolbar"
          aria-label="Map tools"
          aria-orientation="vertical"
        >
          {tools.map((t) => (
            <IconButton
              key={t.tool}
              icon={t.icon}
              label={owner || t.tool === 'select' ? t.label : `${t.label} (${OWNER_ONLY})`}
              pressed={tool === t.tool}
              disabled={
                (!owner && t.tool !== 'select') ||
                ((t.tool === 'edit' || t.tool === 'move') && !hasShape)
              }
              onClick={() => {
                choose(t.tool);
              }}
            />
          ))}
          <IconButton
            icon="Delete"
            label={owner ? 'Delete shape' : `Delete shape (${OWNER_ONLY})`}
            disabled={!owner || !hasShape}
            onClick={() => {
              setShape({ ring: null, circle: null });
              // Back to Select, not straight into drawing: deleting means gone (E61).
              setTool('select');
            }}
          />
        </div>

        <div className="geofence-editor__map">
          <div className="map-bar">
            <Select
              label="Country shown on the map"
              size="sm"
              value={focus ?? ''}
              options={[
                { value: '', label: 'World' },
                ...(regions?.countries ?? [])
                  .map((c) => ({ value: c.key, label: countryName(c.key) }))
                  .sort((a, b) => a.label.localeCompare(b.label)),
              ]}
              onChange={(v) => {
                setFocus(v === '' ? null : v);
              }}
            />
            {focus !== null && (
              <Button
                size="sm"
                variant="ghost"
                icon="Back"
                onClick={() => {
                  setFocus(null);
                }}
              >
                Back to the world
              </Button>
            )}
            {focus === null && (
              <span className="small muted">
                Click a country to open it, with its states and cities.
              </span>
            )}
          </div>
          <Suspense fallback={<Loading kind="chart" label="Loading the map…" />}>
            <GeofenceMap
              tool={tool}
              places={focus === null ? [] : (places.data?.places ?? [])}
              regionKeys={draft.kind === 'region' ? draft.regionKeys : []}
              focusCountry={focus}
              onFocusCountry={setFocus}
              keyByCode={keyByCode}
              ring={draft.kind === 'polygon' ? draft.ring : null}
              circle={draft.kind === 'circle' ? draft.circle : null}
              errorAt={errorAt}
              onToggleRegion={(key) => {
                if (!owner) return;
                setShape({
                  kind: 'region',
                  regionKeys: draft.regionKeys.includes(key)
                    ? draft.regionKeys.filter((k) => k !== key)
                    : [...draft.regionKeys, key],
                });
              }}
              onRing={(ring) => {
                setShape({ kind: 'polygon', ring, circle: null });
              }}
              onCircle={(circle) => {
                setShape({ kind: 'circle', circle, ring: null });
              }}
              onToolDone={() => {
                setTool('select');
              }}
            />
          </Suspense>
          {places.isError && (
            <p className="small muted">Cities are not shown: {places.error.error.message}</p>
          )}
          {errorAt !== null && (
            <Alert tone="error" title="The polygon is not valid">
              {error?.fields.find((f) => f.location !== undefined)?.message ?? 'Invalid geometry'}:
              a ring may not cross itself (F6.AC4). The crossing is marked on the map.
            </Alert>
          )}
        </div>

        <aside className="inspector" aria-label="Geofence details">
          <fieldset className="inspector__group" disabled={!owner}>
            <legend className="sr-only">Details</legend>
            <Field
              label="Name"
              value={draft.name}
              onChange={(name) => {
                set({ name });
              }}
              maxLength={100}
            />
            <Field
              label="Description"
              value={draft.description}
              onChange={(description) => {
                set({ description });
              }}
              maxLength={500}
            />
            <Field
              label="Priority"
              hint="Higher wins where geofences overlap."
              inputMode="numeric"
              value={draft.priority}
              onChange={(priority) => {
                set({ priority });
              }}
              mono
            />
            <div className="field">
              <span className="field__label">Alert priority</span>
              <SegmentedControl
                label="Alert priority"
                value={draft.notify}
                options={[
                  { value: 'high', label: 'High' },
                  { value: 'normal', label: 'Normal' },
                  { value: 'silent', label: 'Silent' },
                ]}
                onChange={(v) => {
                  set({ notify: v === 'normal' || v === 'silent' ? v : 'high' });
                }}
              />
              <span className="field__hint">
                The link's own inside priority can lower it further, never raise it.
              </span>
            </div>
            <LinkScope
              links={links}
              value={draft.links}
              onChange={(next) => {
                set({ links: next });
              }}
            />
            <Switch
              label="Active"
              checked={draft.active}
              onChange={(active) => {
                set({ active });
              }}
            />
          </fieldset>

          <fieldset className="inspector__group" disabled={!owner}>
            <legend className="inspector__legend">{SHAPE[draft.kind].label}</legend>
            {draft.kind === 'region' && (
              <RegionPicker
                regions={regions}
                regionsError={regionsError}
                selected={draft.regionKeys}
                unknown={fence?.unknown_region_keys ?? []}
                focus={focus}
                onChange={(regionKeys) => {
                  setShape({ kind: 'region', regionKeys });
                }}
              />
            )}
            {draft.kind === 'polygon' && (
              <VertexList
                ring={draft.ring ?? []}
                holes={draft.shapeDirty ? 0 : holes}
                onChange={(ring) => {
                  setShape({ ring });
                }}
              />
            )}
            {draft.kind === 'circle' && (
              <CircleFields
                circle={draft.circle}
                onChange={(circle) => {
                  setShape({ circle });
                }}
              />
            )}
          </fieldset>

          {problem !== null && <p className="error-text small">{problem}</p>}
          {error !== null && errorAt === null && <ErrorNotice error={error} />}
          {error !== null && error.fields.length > 0 && errorAt === null && (
            <ul className="plain small">
              {error.fields.map((f) => (
                <li key={`${f.field}-${f.code}`}>
                  <span className="t-mono">{f.field}</span>: {f.message}
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      {fence !== null && (
        <ConfirmDialog
          open={deleting}
          title={`Delete ${fence.name}?`}
          confirmLabel="Delete"
          danger
          typed={{ label: `Type ${fence.name} to confirm`, phrase: fence.name }}
          busy={deleteBusy}
          error={deleteError}
          onClose={() => {
            setDeleting(false);
          }}
          onConfirm={() => {
            void remove();
          }}
        >
          Visits are no longer evaluated against it. Visits it already matched keep the match. This
          cannot be undone; an export first keeps a copy.
        </ConfirmDialog>
      )}
    </div>
  );
}

function LinkScope({
  links,
  value,
  onChange,
}: {
  readonly links: readonly LinkChoice[];
  readonly value: readonly string[] | null;
  readonly onChange: (next: readonly string[] | null) => void;
}): React.JSX.Element {
  const live = links.filter((l) => l.archived_at === null || value?.includes(l.id) === true);
  return (
    <div className="field">
      <span className="field__label">Links</span>
      <Checkbox
        label="All links"
        checked={value === null}
        onChange={(all) => {
          onChange(all ? null : []);
        }}
      />
      {value !== null && (
        <ul className="plain inspector__links">
          {live.map((l) => (
            <li key={l.id}>
              <Checkbox
                label={
                  <>
                    {l.label} <span className="t-meta t-mono">{l.slug}</span>
                  </>
                }
                checked={value.includes(l.id)}
                onChange={(on) => {
                  onChange(on ? [...value, l.id] : value.filter((id) => id !== l.id));
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {value !== null && value.length === 0 && (
        <span className="field__hint">Choose at least one link, or all links.</span>
      )}
    </div>
  );
}

function RegionPicker({
  regions,
  regionsError,
  selected,
  unknown,
  focus,
  onChange,
}: {
  readonly regions: Regions | null;
  readonly regionsError: ApiError | null;
  readonly selected: readonly string[];
  readonly unknown: readonly string[];
  readonly focus: string | null;
  readonly onChange: (keys: readonly string[]) => void;
}): React.JSX.Element {
  const [search, setSearch] = useState('');
  const matches = useMemo(() => {
    if (regions === null || search.trim().length < 2) return [];
    const q = search.trim().toLowerCase();
    const countries = regions.countries
      .map((c) => ({ key: c.key, label: countryName(c.key) }))
      .filter((c) => c.label.toLowerCase().includes(q) || c.key.toLowerCase() === q);
    const divisions = regions.divisions
      .filter((d) => d.name.toLowerCase().includes(q))
      .map((d) => ({ key: d.key, label: regionLabel(d.key) }));
    return [...countries, ...divisions].slice(0, 20);
  }, [regions, search]);

  if (regionsError !== null) {
    return (
      <div className="stack-sm">
        <ErrorNotice error={regionsError} />
        <p className="small muted">
          Region geofences need the region list; polygons and circles do not.
        </p>
      </div>
    );
  }
  return (
    <div className="stack-sm region-picker">
      {selected.length === 0 ? (
        <p className="small muted">
          Nothing picked yet. Open a country on the map and click its states, or search below.
        </p>
      ) : (
        <ul className="plain region-picker__chips" aria-label="Picked regions">
          {selected.map((key) => (
            <li key={key} className="region-chip">
              <Badge tone={unknown.includes(key) ? 'warn' : 'info'}>{regionLabel(key)}</Badge>
              <IconButton
                icon="Close"
                size="sm"
                label={`Remove ${regionLabel(key)}`}
                onClick={() => {
                  onChange(selected.filter((k) => k !== key));
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {unknown.some((k) => selected.includes(k)) && (
        <Alert tone="warn" title="A region is no longer recognised">
          GeoNames may have renamed it. It is kept, but it will never match until it is replaced.
        </Alert>
      )}
      {regions !== null && focus !== null && (
        <Button
          size="sm"
          icon={selected.includes(focus) ? 'Close' : 'Add'}
          onClick={() => {
            onChange(
              selected.includes(focus) ? selected.filter((k) => k !== focus) : [...selected, focus],
            );
          }}
        >
          {selected.includes(focus)
            ? `Remove all of ${countryName(focus)}`
            : `Pick all of ${countryName(focus)}`}
        </Button>
      )}
      <SearchInput
        label="Search countries and states"
        placeholder="Search countries and states"
        value={search}
        onChange={setSearch}
      />
      {matches.length > 0 && (
        <ul className="plain region-picker__results">
          {matches.map((m) => (
            <li key={m.key}>
              <span>{m.label}</span>
              {selected.includes(m.key) ? (
                <Badge>Picked</Badge>
              ) : (
                <Button
                  size="sm"
                  icon="Add"
                  onClick={() => {
                    onChange([...selected, m.key]);
                  }}
                >
                  Add
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {search.trim().length >= 2 && matches.length === 0 && regions !== null && (
        <p className="small muted">No country or state matches “{search.trim()}”.</p>
      )}
    </div>
  );
}

function parseNumber(value: string): number | null {
  const n = Number(value.trim());
  return value.trim() === '' || !Number.isFinite(n) ? null : n;
}

function VertexList({
  ring,
  holes,
  onChange,
}: {
  readonly ring: Ring;
  readonly holes: number;
  readonly onChange: (ring: Ring) => void;
}): React.JSX.Element {
  const [text, setText] = useState<readonly (readonly [string, string])[]>(() =>
    ring.map(([lat, lng]) => [String(lat), String(lng)] as const),
  );
  useEffect(() => {
    setText(ring.map(([lat, lng]) => [String(round6(lat)), String(round6(lng))] as const));
  }, [ring]);

  const commit = (rows: readonly (readonly [string, string])[]): void => {
    setText(rows);
    const parsed = rows.map(([a, b]) => [parseNumber(a), parseNumber(b)] as const);
    if (
      parsed.every(([a, b]) => a !== null && b !== null && Math.abs(a) <= 90 && Math.abs(b) <= 180)
    ) {
      onChange(parsed.map(([a, b]) => [a ?? 0, b ?? 0] as const));
    }
  };

  return (
    <div className="stack-sm">
      <p className="small muted">
        Draw on the map, or type each vertex as latitude and longitude in decimal degrees. The ring
        closes itself.
      </p>
      {holes > 0 && (
        <Alert
          tone="info"
          title={
            holes === 1 ? 'This polygon has a hole' : `This polygon has ${String(holes)} holes`
          }
        >
          Imported holes are kept as long as the shape is not changed here.
        </Alert>
      )}
      <ol className="vertex-list">
        {text.map(([lat, lng], i) => (
          // A vertex has no identity but its place in the ring.
          <li key={i} className="vertex-row">
            <span className="t-meta">{i + 1}</span>
            <Input
              size="sm"
              aria-label={`Vertex ${String(i + 1)} latitude`}
              value={lat}
              onChange={(e) => {
                commit(text.map((row, j) => (j === i ? [e.target.value, row[1]] : row)));
              }}
            />
            <Input
              size="sm"
              aria-label={`Vertex ${String(i + 1)} longitude`}
              value={lng}
              onChange={(e) => {
                commit(text.map((row, j) => (j === i ? [row[0], e.target.value] : row)));
              }}
            />
            <IconButton
              icon="Close"
              size="sm"
              label={`Remove vertex ${String(i + 1)}`}
              onClick={() => {
                commit(text.filter((_, j) => j !== i));
              }}
            />
          </li>
        ))}
      </ol>
      <Button
        size="sm"
        icon="Add"
        onClick={() => {
          const last = text[text.length - 1];
          commit([...text, last ?? ['', '']]);
        }}
      >
        Add vertex
      </Button>
    </div>
  );
}

function CircleFields({
  circle,
  onChange,
}: {
  readonly circle: CircleShape | null;
  readonly onChange: (circle: CircleShape) => void;
}): React.JSX.Element {
  const [lat, setLat] = useState(circle === null ? '' : String(round6(circle.lat)));
  const [lng, setLng] = useState(circle === null ? '' : String(round6(circle.lng)));
  const [radius, setRadius] = useState(circle === null ? '' : String(circle.radius_m));
  // Whether the fields hold something the person typed and the circle does not have yet.
  // Only that is ever applied: fields that merely still show a deleted circle must not put it
  // back (E61).
  const typed = useRef(false);
  useEffect(() => {
    typed.current = false;
    setLat(circle === null ? '' : String(round6(circle.lat)));
    setLng(circle === null ? '' : String(round6(circle.lng)));
    setRadius(circle === null ? '' : String(circle.radius_m));
  }, [circle]);

  // Typed values are applied half a second after the last keystroke, and only if they
  // changed: "13.18" would otherwise move the circle to latitude 1, then 13, then 13.1, with
  // the map following it each time.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  useEffect(() => {
    if (!typed.current) return undefined;
    const timer = window.setTimeout(() => {
      const [la, ln, ra] = [parseNumber(lat), parseNumber(lng), parseNumber(radius)];
      if (la === null || ln === null || ra === null || Math.abs(la) > 90 || Math.abs(ln) > 180) {
        return;
      }
      const next = { lat: la, lng: ln, radius_m: Math.round(ra) };
      const same =
        circle !== null &&
        round6(circle.lat) === round6(next.lat) &&
        round6(circle.lng) === round6(next.lng) &&
        circle.radius_m === next.radius_m;
      if (!same) onChangeRef.current(next);
    }, TYPING_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [lat, lng, radius, circle]);
  return (
    <div className="stack-sm">
      <p className="small muted">Draw on the map, or type the centre and the radius.</p>
      <div className="circle-fields">
        <Field
          label="Latitude"
          inputMode="numeric"
          mono
          value={lat}
          onChange={(v) => {
            typed.current = true;
            setLat(v);
          }}
        />
        <Field
          label="Longitude"
          inputMode="numeric"
          mono
          value={lng}
          onChange={(v) => {
            typed.current = true;
            setLng(v);
          }}
        />
        <Field
          label="Radius (m)"
          inputMode="numeric"
          mono
          hint="50 m to 1,000 km"
          value={radius}
          onChange={(v) => {
            typed.current = true;
            setRadius(v);
          }}
        />
      </div>
    </div>
  );
}
