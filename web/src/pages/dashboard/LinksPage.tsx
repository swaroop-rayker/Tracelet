/**
 * Links (DESIGN §10.11, §12 E20; F1, F10.AC6, SPEC §11 row 25): every tracking link, where it
 * sends visitors, and its all-time visit count, each opening its own dashboard -- and the
 * owner's writes: **New link**, and per row Edit, Make default, Archive and **Delete
 * permanently** (which first shows exactly what would go, and wants the slug typed, UI-16).
 * Archived links keep only Delete permanently. An analyst sees every write disabled with the
 * reason (UI-17). The one-click setting is still "Asks for location" (F1.AC11, ADR-0021).
 *
 * The request always names `include_archived`, so its cache entry is never the link
 * selector's, which parses the same endpoint with a narrower schema.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import type { ApiError } from '@/api/client';
import {
  archiveLink,
  createLink,
  deleteLink,
  deletePreviewSchema,
  linksFullSchema,
  makeDefault,
  setAskLocation,
  updateLink,
  type DeletePreview,
  type LinkForm,
  type LinkFull,
  type Priority,
} from '@/api/links';
import { useApi } from '@/api/query';
import type { LinkSummary } from '@/api/schemas';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  Dialog,
  ErrorNotice,
  Field,
  Menu,
  MenuItem,
  Select,
  Switch,
  Timestamp,
  toast,
} from '@/components/ui';
import { count } from '@/format';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

const LINKS = '/api/v1/links';
const OWNER_ONLY = 'Only the owner can change links.';

/** "instagram.com/p/xyz…": enough to recognise, never the whole URL in a cell. */
function destination(url: string): string {
  try {
    const parsed = new URL(url);
    const rest = `${parsed.pathname}${parsed.search}`;
    const shown = `${parsed.host}${rest === '/' ? '' : rest}`;
    return shown.length > 40 ? `${shown.slice(0, 39)}…` : shown;
  } catch {
    return url;
  }
}

export function LinkStatus({ link }: { readonly link: LinkSummary }): React.JSX.Element {
  return (
    <span className="badges">
      {link.archived_at !== null ? (
        <Badge>Archived</Badge>
      ) : link.is_active ? (
        <Badge tone="ok">Active</Badge>
      ) : (
        <Badge tone="warn">Inactive</Badge>
      )}
      {link.is_default && <Badge tone="info">Default</Badge>}
    </span>
  );
}

/**
 * Whether the link asks visitors for their location (F1.AC11, ADR-0021): a consent screen and
 * the browser prompt, waiting up to 15 s. Owner only, optimistic and reversible (UI-13); an
 * analyst sees the setting as text (UI-17).
 */
function AskCell({ link }: { readonly link: LinkFull }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [value, setValue] = useState(link.ask_location);
  if (me.role !== 'owner' || link.archived_at !== null) {
    return <span>{link.ask_location ? 'Yes' : 'No'}</span>;
  }
  return (
    <Switch
      label={`${link.label} asks for location`}
      hideLabel
      checked={value}
      onChange={(next) => {
        setValue(next);
        void setAskLocation(me.csrf_token, link.id, next).then((result) => {
          if (!result.ok) {
            setValue(!next);
            toast(`Could not change ${link.label}: ${result.error.message}`);
            return;
          }
          setValue(result.data.ask_location);
          toast(
            result.data.ask_location
              ? `${link.label} now asks visitors for their location.`
              : `${link.label} no longer asks for location.`,
          );
          void client.invalidateQueries({ queryKey: [LINKS] });
        });
      }}
    />
  );
}

type Editing = { readonly mode: 'new' } | { readonly mode: 'edit'; readonly link: LinkFull };

export default function LinksPage(): React.JSX.Element {
  const [archived, setArchived] = useState(false);
  const { me } = useSession();
  const owner = me.role === 'owner';
  const location = useLocation();
  const client = useQueryClient();
  const query = useApi(
    LINKS,
    new URLSearchParams({ include_archived: String(archived) }),
    linksFullSchema,
  );
  const [editing, setEditing] = useState<Editing | null>(null);
  const [archiving, setArchiving] = useState<LinkFull | null>(null);
  const [deleting, setDeleting] = useState<LinkFull | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const refresh = async (): Promise<void> => {
    await client.invalidateQueries({ queryKey: [LINKS] });
  };

  async function archive(link: LinkFull): Promise<void> {
    setBusy(true);
    const result = await archiveLink(me.csrf_token, link.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setArchiving(null);
    toast(`${link.label} archived. Its address now answers “not found”; its visits stay.`);
    await refresh();
  }

  async function setDefault(link: LinkFull): Promise<void> {
    const result = await makeDefault(me.csrf_token, link.id);
    if (!result.ok) {
      toast(`Could not make ${link.label} the default: ${result.error.message}`);
      return;
    }
    toast(`${link.label} is now the default link.`);
    await refresh();
  }

  return (
    <div className="page">
      <PageHeader
        title="Links"
        description="Every tracking link and where it sends visitors. Counts are all-time."
        actions={
          <span className="row-actions">
            <Switch label="Show archived" checked={archived} onChange={setArchived} />
            <Button
              variant="primary"
              icon="Add"
              disabledReason={owner ? null : OWNER_ONLY}
              onClick={() => {
                setEditing({ mode: 'new' });
              }}
            >
              New link
            </Button>
          </span>
        }
      />
      <Panel
        query={query}
        kind="table"
        title="Tracking links"
        description="Open a link for its own dashboard, with the period and filters you are using."
        isEmpty={(d) => d.length === 0}
        empty={
          archived
            ? 'No links yet. Create the first with “New link”.'
            : 'No active links. Create one, or turn on “Show archived” to see archived ones.'
        }
      >
        {(links) => (
          <DataTable
            caption="Tracking links"
            rowKey={(l) => l.id}
            rows={links}
            columns={[
              {
                key: 'link',
                header: 'Link',
                render: (l) => (
                  <span className="link-cell">
                    <Link
                      className="link"
                      to={{ pathname: `/links/${l.slug}`, search: location.search }}
                    >
                      {l.label}
                    </Link>
                    <span className="t-meta mono">{l.slug}</span>
                  </span>
                ),
              },
              {
                key: 'destination',
                header: 'Destination',
                render: (l) => (
                  <span className="muted" title={l.destination_url}>
                    {destination(l.destination_url)}
                  </span>
                ),
              },
              { key: 'status', header: 'Status', render: (l) => <LinkStatus link={l} /> },
              {
                key: 'ask',
                header: 'Asks for location',
                render: (l) => <AskCell link={l} />,
              },
              {
                key: 'visits',
                header: 'Visits',
                numeric: true,
                render: (l) => count(l.visit_count),
              },
              {
                key: 'created',
                header: 'Created',
                render: (l) => <Timestamp iso={l.created_at} zone={me.timezone} />,
              },
              {
                key: 'actions',
                header: <span className="sr-only">Actions</span>,
                render: (l) => (
                  <Menu label={`Actions for ${l.label}`} icon="More" iconOnly size="sm" align="end">
                    {(close) => (
                      <>
                        {l.archived_at === null && (
                          <>
                            <MenuItem
                              icon="ToolEdit"
                              disabled={!owner}
                              {...(owner ? {} : { hint: 'Owner only' })}
                              onSelect={() => {
                                close();
                                setEditing({ mode: 'edit', link: l });
                              }}
                            >
                              Edit…
                            </MenuItem>
                            {!l.is_default && (
                              <MenuItem
                                icon="Verified"
                                disabled={!owner}
                                {...(owner ? {} : { hint: 'Owner only' })}
                                onSelect={() => {
                                  close();
                                  void setDefault(l);
                                }}
                              >
                                Make default
                              </MenuItem>
                            )}
                            <MenuItem
                              icon="Hide"
                              disabled={!owner}
                              {...(owner ? {} : { hint: 'Owner only' })}
                              onSelect={() => {
                                close();
                                setError(null);
                                setArchiving(l);
                              }}
                            >
                              Archive…
                            </MenuItem>
                          </>
                        )}
                        <MenuItem
                          icon="Delete"
                          danger
                          disabled={!owner}
                          {...(owner ? {} : { hint: 'Owner only' })}
                          onSelect={() => {
                            close();
                            setDeleting(l);
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

      {editing !== null && (
        <LinkDialog
          editing={editing}
          onClose={() => {
            setEditing(null);
          }}
          onSaved={() => void refresh()}
        />
      )}

      <ConfirmDialog
        open={archiving !== null}
        title={archiving === null ? '' : `Archive ${archiving.label}?`}
        confirmLabel="Archive"
        busyLabel="Archiving…"
        danger={false}
        busy={busy}
        error={error}
        onConfirm={() => {
          if (archiving !== null) void archive(archiving);
        }}
        onClose={() => {
          setArchiving(null);
          setError(null);
        }}
      >
        Its address <span className="t-mono">/r/{archiving?.slug}</span> will answer “not found” at
        once. Its visits and figures stay; it can still be deleted permanently later.
      </ConfirmDialog>

      {deleting !== null && (
        <DeleteDialog
          link={deleting}
          onClose={() => {
            setDeleting(null);
          }}
          onDeleted={() => void refresh()}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// New and Edit
// ---------------------------------------------------------------------------

const PRIORITIES: readonly { readonly value: Priority; readonly label: string }[] = [
  { value: 'high', label: 'High' },
  { value: 'normal', label: 'Normal' },
  { value: 'silent', label: 'Silent' },
];

function LinkDialog({
  editing,
  onClose,
  onSaved,
}: {
  readonly editing: Editing;
  readonly onClose: () => void;
  readonly onSaved: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const existing = editing.mode === 'edit' ? editing.link : null;
  const [label, setLabel] = useState(existing?.label ?? '');
  const [slug, setSlug] = useState(existing?.slug ?? '');
  const [dest, setDest] = useState(existing?.destination_url ?? '');
  const [active, setActive] = useState(existing?.is_active ?? true);
  const [interstitial, setInterstitial] = useState(String(existing?.interstitial_ms ?? 700));
  const [ask, setAsk] = useState(existing?.ask_location ?? false);
  const [inside, setInside] = useState<Priority>(existing?.notify_policy.inside ?? 'high');
  const [outside, setOutside] = useState<Priority>(existing?.notify_policy.outside ?? 'normal');
  const [undetermined, setUndetermined] = useState<Priority>(
    existing?.notify_policy.undetermined ?? 'normal',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const fieldError = (name: string): string | null =>
    error?.fields.find((f) => f.field === name || f.field.endsWith(`.${name}`))?.message ?? null;
  const slugChanged = existing !== null && slug.trim().toLowerCase() !== existing.slug;

  async function save(): Promise<void> {
    const form: LinkForm = {
      slug: slug.trim().toLowerCase(),
      label: label.trim(),
      destination_url: dest.trim(),
      is_active: active,
      interstitial_ms: Number(interstitial),
      ask_location: ask,
      notify_policy: { inside, outside, undetermined, automated: 'silent' },
    };
    setBusy(true);
    const result =
      existing === null
        ? await createLink(me.csrf_token, form)
        : await updateLink(me.csrf_token, existing.id, form);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(
      existing === null
        ? `${result.data.label} created: ${result.data.capture_url}`
        : `${result.data.label} saved. It applies to the next visit.`,
    );
    onSaved();
    onClose();
  }

  return (
    <Dialog
      open
      size="md"
      onClose={onClose}
      dismissible={!busy}
      title={existing === null ? 'New link' : `Edit ${existing.label}`}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} busyLabel="Saving…" onClick={() => void save()}>
            {existing === null ? 'Create link' : 'Save'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <Field
            label="Label"
            value={label}
            onChange={setLabel}
            autoFocus
            maxLength={120}
            error={fieldError('label')}
          />
          <Field
            label="Slug"
            value={slug}
            onChange={setSlug}
            mono
            maxLength={32}
            hint={`4–32 of a–z, 0–9 and -. The address is ${window.location.origin}/r/${slug.trim().toLowerCase() || '…'}`}
            error={fieldError('slug')}
          />
        </div>
        {slugChanged && (
          <Alert tone="warn">
            The old address /r/{existing.slug} stops working at once. To keep it working, clone the
            link instead.
          </Alert>
        )}
        <Field
          label="Destination"
          value={dest}
          onChange={setDest}
          maxLength={2048}
          hint="Where visitors are sent: https only, a public address."
          error={fieldError('destination_url')}
        />
        <div className="form-grid">
          <Field
            label="Interstitial, ms"
            type="number"
            inputMode="numeric"
            value={interstitial}
            onChange={setInterstitial}
            hint="How long the page waits before sending the visitor on: 300 to 1500."
            error={fieldError('interstitial_ms')}
          />
          <div className="stack-sm">
            <Switch label="Active" checked={active} onChange={setActive} />
            <Switch label="Ask for location" checked={ask} onChange={setAsk} />
          </div>
        </div>
        <div className="form-grid">
          <Select
            label="Alert when inside a geofence"
            value={inside}
            onChange={(v) => {
              setInside(v as Priority);
            }}
            options={PRIORITIES}
          />
          <Select
            label="Alert when outside"
            value={outside}
            onChange={(v) => {
              setOutside(v as Priority);
            }}
            options={PRIORITIES}
          />
          <Select
            label="Alert when not confirmed"
            value={undetermined}
            onChange={(v) => {
              setUndetermined(v as Priority);
            }}
            options={PRIORITIES}
          />
        </div>
        {error !== null && error.fields.length === 0 && <ErrorNotice error={error} />}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete permanently
// ---------------------------------------------------------------------------

function DeleteDialog({
  link,
  onClose,
  onDeleted,
}: {
  readonly link: LinkFull;
  readonly onClose: () => void;
  readonly onDeleted: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const preview = useApi(`${LINKS}/${link.id}/delete-preview`, null, deletePreviewSchema);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function remove(): Promise<void> {
    setBusy(true);
    const result = await deleteLink(me.csrf_token, link.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(`${link.label} and its history are deleted.`);
    onDeleted();
    onClose();
  }

  const p = preview.data;
  return (
    <ConfirmDialog
      open
      title={`Delete ${link.label} permanently?`}
      confirmLabel="Delete permanently"
      busyLabel="Deleting…"
      typed={p ? { label: `Type ${link.slug} to confirm`, phrase: link.slug } : undefined}
      busy={busy || p === undefined}
      error={error ?? (preview.isError ? preview.error.error : null)}
      onConfirm={() => void remove()}
      onClose={onClose}
    >
      {p === undefined ? <p>Counting what would go…</p> : <DeleteSummary preview={p} />}
    </ConfirmDialog>
  );
}

/** ", its 3 visits, 12 location candidates and 40 rows of its figures": only what exists. */
function deleted(p: DeletePreview): string {
  const plural = (n: number, one: string, many: string): string =>
    `${count(n)} ${n === 1 ? one : many}`;
  const parts = [
    p.visits > 0 ? plural(p.visits, 'visit', 'visits') : null,
    p.visit_candidates > 0
      ? plural(p.visit_candidates, 'location candidate', 'location candidates')
      : null,
    p.rollup_rows > 0 ? `${plural(p.rollup_rows, 'row', 'rows')} of its daily figures` : null,
  ].filter((x): x is string => x !== null);
  if (parts.length === 0) return ' (it has no visits)';
  const last = parts.pop();
  return `, its ${parts.length > 0 ? `${parts.join(', ')} and ${last ?? ''}` : (last ?? '')}`;
}

function DeleteSummary({ preview }: { readonly preview: DeletePreview }): React.JSX.Element {
  return (
    <div className="stack-sm">
      <p>
        This deletes <span className="t-mono">{preview.slug}</span>
        {deleted(preview)}. It cannot be undone; a backup taken before keeps it.
      </p>
      {preview.geofences_updated.length > 0 && (
        <p>
          It is removed from {preview.geofences_updated.map((g) => g.name).join(', ')}, which keep
          their other links.
        </p>
      )}
      {preview.geofences_deactivated.length > 0 && (
        <p>
          {preview.geofences_deactivated.map((g) => g.name).join(', ')} applied to this link alone
          and will be switched off.
        </p>
      )}
    </div>
  );
}
