/**
 * Alerts (DESIGN §16, F7): where Telegram alerts go, when quiet hours hold them, which other
 * kinds of message are on (M7.5, F7.AC10–F7.AC15), and every delivery with its outcome.
 *
 * The bot token and the chat are deployment secrets (F12.AC3): this page says whether they
 * are set, never what they are. Changing quiet hours, sending the test message and retrying a
 * dead letter are the owner's (invariant 9): an analyst sees them disabled with the reason
 * (UI-17).
 *
 * The delivery log polls every 15 seconds while the tab is visible and says how fresh it is
 * (UI-18). Its status filter and page are in the URL (UI-9), so the header bell can link
 * straight to the dead letters.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { ApiError } from '@/api/client';
import {
  notificationSettingsSchema,
  outboxSchema,
  retryDelivery,
  sendTestMessage,
  updateAlertTypes,
  updateQuietHours,
  type AlertTypes,
  type Delivery,
  type NotificationSettings,
} from '@/api/geofences';
import { useApi } from '@/api/query';
import { Panel } from '@/components/Panel';
import { Freshness } from '@/components/shell/Freshness';
import { PageHeader } from '@/components/shell/PageHeader';
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  SegmentedControl,
  Select,
  SettingRow,
  StatStrip,
  Switch,
  Timestamp,
  toast,
} from '@/components/ui';
import { count } from '@/format';
import { PRIORITY, toned } from '@/pages/configure/geofence-format';
import { useSession } from '@/session';

const SETTINGS = '/api/v1/notifications/settings';
const OUTBOX = '/api/v1/health/outbox';
const OWNER_ONLY = 'Only the owner can change alerts.';
const POLL_MS = 15_000;
const PAGE = 50;

const FILTERS: readonly { readonly value: string; readonly label: string }[] = [
  { value: '', label: 'All' },
  { value: 'dead', label: 'Dead-lettered' },
  { value: 'failed', label: 'Retrying' },
  { value: 'pending', label: 'Waiting' },
  { value: 'done', label: 'Delivered' },
];

export default function AlertsPage(): React.JSX.Element {
  const settings = useApi(SETTINGS, null, notificationSettingsSchema);
  return (
    <div className="page">
      <PageHeader
        title="Alerts"
        description="Where Telegram alerts go, when they wait, and every delivery."
      />
      {settings.isPending && <Loading kind="text" label="Loading alert settings…" />}
      {settings.isError && <ErrorNotice error={settings.error.error} />}
      {settings.data !== undefined && <SettingsCards settings={settings.data} />}
      <DeliveryLog />
    </div>
  );
}

function SettingsCards({
  settings,
}: {
  readonly settings: NotificationSettings;
}): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const { telegram } = settings;
  const configured = telegram.bot_token_set && telegram.chat_id_set;
  return (
    <>
      <Card title="Telegram" description="Alerts go to one chat, set when the server is deployed.">
        <SettingRow
          title="Chat"
          description={
            configured
              ? 'The bot token and the owner chat are set on the server.'
              : 'Set TRACELET_TELEGRAM_BOT_TOKEN and TRACELET_TELEGRAM_OWNER_CHAT_ID on the server. Until then, alerts wait in the queue and then dead-letter.'
          }
        >
          <span className="badges">
            {configured ? (
              <Badge tone="ok">Configured</Badge>
            ) : (
              <Badge tone="warn">Not configured</Badge>
            )}
            {configured &&
              (telegram.chat_verified ? (
                <Badge tone="ok">Verified</Badge>
              ) : (
                <Badge>Not verified by an admin</Badge>
              ))}
          </span>
        </SettingRow>
        <TestMessageRow owner={owner} configured={configured} />
      </Card>
      <QuietHoursCard settings={settings} owner={owner} />
      <AlertTypesCard settings={settings} owner={owner} />
    </>
  );
}

function TestMessageRow({
  owner,
  configured,
}: {
  readonly owner: boolean;
  readonly configured: boolean;
}): React.JSX.Element {
  const { me } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [delivered, setDelivered] = useState<string | null>(null);
  return (
    <SettingRow
      title="Test message"
      description="Sends one message to the chat now, to prove the token and the chat (F7.AC8)."
    >
      <div className="stack-sm">
        <Button
          icon="Send"
          busy={busy}
          busyLabel="Sending…"
          disabledReason={
            owner ? (configured ? null : 'Telegram is not configured on the server.') : OWNER_ONLY
          }
          onClick={() => {
            setBusy(true);
            setError(null);
            setDelivered(null);
            void sendTestMessage(me.csrf_token).then((result) => {
              setBusy(false);
              if (result.ok) setDelivered(result.data.delivered_at);
              else setError(result.error);
            });
          }}
        >
          Send test message
        </Button>
        {delivered !== null && (
          <p className="small" role="status">
            Delivered <Timestamp iso={delivered} zone={me.timezone} mode="time" />
          </p>
        )}
        {error !== null && <ErrorNotice error={error} />}
      </div>
    </SettingRow>
  );
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function zones(current: string, mine: string): readonly { value: string; label: string }[] {
  const all = ['Asia/Kolkata', 'UTC', mine, current];
  return [...new Set(all)].map((z) => ({ value: z, label: z }));
}

function QuietHoursCard({
  settings,
  owner,
}: {
  readonly settings: NotificationSettings;
  readonly owner: boolean;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const saved = settings.quiet_hours;
  const [enabled, setEnabled] = useState(saved.enabled);
  const [start, setStart] = useState(saved.start);
  const [end, setEnd] = useState(saved.end);
  const [timezone, setTimezone] = useState(saved.timezone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setEnabled(saved.enabled);
    setStart(saved.start);
    setEnd(saved.end);
    setTimezone(saved.timezone);
  }, [saved.enabled, saved.start, saved.end, saved.timezone]);

  const changed =
    enabled !== saved.enabled ||
    start !== saved.start ||
    end !== saved.end ||
    timezone !== saved.timezone;
  const startError = HHMM.test(start) ? null : 'A time is HH:MM, 00:00 to 23:59.';
  const endError = HHMM.test(end) ? null : 'A time is HH:MM, 00:00 to 23:59.';

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await updateQuietHours(me.csrf_token, { enabled, start, end, timezone });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Quiet hours saved.');
    void client.invalidateQueries({ queryKey: [SETTINGS] });
  }

  return (
    <Card
      title="Quiet hours"
      description="Hold normal alerts in a window and send them when it closes. High-priority alerts are never held (F7.AC9)."
      actions={saved.active_now ? <Badge tone="info">Quiet now</Badge> : undefined}
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (startError === null && endError === null) void save();
        }}
      >
        <Switch
          label="Hold normal alerts during quiet hours"
          checked={enabled}
          onChange={setEnabled}
          disabled={!owner}
        />
        <div className="quiet-hours__times">
          <Field
            label="From"
            value={start}
            onChange={setStart}
            mono
            placeholder="23:00"
            maxLength={5}
            disabled={!owner}
            error={startError}
          />
          <Field
            label="Until"
            value={end}
            onChange={setEnd}
            mono
            placeholder="07:00"
            maxLength={5}
            disabled={!owner}
            error={endError}
            {...(start > end ? { hint: 'Crosses midnight.' } : {})}
          />
          <Select
            label="Time zone"
            value={timezone}
            options={zones(saved.timezone, me.timezone)}
            onChange={setTimezone}
          />
        </div>
        {error !== null && <ErrorNotice error={error} />}
        <div>
          <Button
            variant="primary"
            type="submit"
            busy={busy}
            busyLabel="Saving…"
            disabled={owner && !changed}
            disabledReason={owner ? null : OWNER_ONLY}
          >
            Save quiet hours
          </Button>
        </div>
      </form>
    </Card>
  );
}

/** Whole numbers only, within bounds; the message the Field shows otherwise. */
function wholeIn(value: string, low: number, high: number): string | null {
  const n = Number(value);
  return /^\d+$/.test(value) && n >= low && n <= high
    ? null
    : `A whole number from ${String(low)} to ${String(high)}.`;
}

function numberIn(value: string, low: number, high: number): string | null {
  const n = Number(value);
  return value.trim() !== '' && Number.isFinite(n) && n >= low && n <= high
    ? null
    : `A number from ${String(low)} to ${String(high)}.`;
}

/**
 * F7.AC10–F7.AC15 (DESIGN §16 M7.5): four more kinds of message, each off until the owner
 * switches it on. One form and one Save, like quiet hours; numbers are edited as text so a
 * half-typed value can be shown with its error rather than silently coerced.
 */
function AlertTypesCard({
  settings,
  owner,
}: {
  readonly settings: NotificationSettings;
  readonly owner: boolean;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const saved = settings.alert_types;
  const [digestOn, setDigestOn] = useState(saved.digest.enabled);
  const [digestAt, setDigestAt] = useState(saved.digest.at);
  const [spikeOn, setSpikeOn] = useState(saved.spike.enabled);
  const [floor, setFloor] = useState(String(saved.spike.floor));
  const [k, setK] = useState(String(saved.spike.k));
  const [placeOn, setPlaceOn] = useState(saved.new_place.enabled);
  const [returningOn, setReturningOn] = useState(saved.returning.enabled);
  const [afterDays, setAfterDays] = useState(String(saved.returning.after_days));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setDigestOn(saved.digest.enabled);
    setDigestAt(saved.digest.at);
    setSpikeOn(saved.spike.enabled);
    setFloor(String(saved.spike.floor));
    setK(String(saved.spike.k));
    setPlaceOn(saved.new_place.enabled);
    setReturningOn(saved.returning.enabled);
    setAfterDays(String(saved.returning.after_days));
  }, [saved]);

  const atError = HHMM.test(digestAt) ? null : 'A time is HH:MM, 00:00 to 23:59.';
  const floorError = wholeIn(floor, 1, 1000);
  const kError = numberIn(k, 1.5, 20);
  const daysError = wholeIn(afterDays, 1, 90);
  const valid = atError === null && floorError === null && kError === null && daysError === null;
  const value: AlertTypes = {
    digest: { enabled: digestOn, at: digestAt },
    spike: { enabled: spikeOn, floor: Number(floor), k: Number(k) },
    new_place: { enabled: placeOn },
    returning: { enabled: returningOn, after_days: Number(afterDays) },
  };
  const changed = JSON.stringify(value) !== JSON.stringify(saved);

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await updateAlertTypes(me.csrf_token, value);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Alert types saved.');
    void client.invalidateQueries({ queryKey: [SETTINGS] });
  }

  return (
    <Card
      title="Alert types"
      description="More kinds of message. Each is off until you switch it on, and counts people only, never bots."
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) void save();
        }}
      >
        <SettingRow
          title="Daily digest"
          description={`Yesterday's visits, the human share, top states (best guess) and links, and any dead letters. Held by quiet hours. The time is in ${me.reporting_tz}.`}
        >
          <div className="alert-type__fields">
            <Field
              label="At"
              value={digestAt}
              onChange={setDigestAt}
              mono
              placeholder="09:00"
              maxLength={5}
              disabled={!owner}
              error={atError}
            />
            <Switch
              label="Send the daily digest"
              hideLabel
              checked={digestOn}
              onChange={setDigestOn}
              disabled={!owner}
            />
          </div>
        </SettingRow>
        <SettingRow
          title="Volume spike"
          description="A link far busier than usual for this time of day: at least this many people in 60 minutes, and more than this many times the median of the same 60 minutes over the last 7 days."
        >
          <div className="alert-type__fields">
            <Field
              label="At least"
              value={floor}
              onChange={setFloor}
              inputMode="numeric"
              maxLength={4}
              disabled={!owner}
              error={floorError}
            />
            <Field
              label="Times usual"
              value={k}
              onChange={setK}
              inputMode="text"
              maxLength={4}
              disabled={!owner}
              error={kError}
            />
            <Switch
              label="Send volume spikes"
              hideLabel
              checked={spikeOn}
              onChange={setSpikeOn}
              disabled={!owner}
            />
          </div>
        </SettingRow>
        <SettingRow
          title="First visit from a new place"
          description="A country or state confirmed for the first time on a link. Added to the visit's alert; sent alone only if that visitor's alert already went out today."
        >
          <div className="alert-type__fields">
            <Switch
              label="Say when a link gets its first visit from a new place"
              hideLabel
              checked={placeOn}
              onChange={setPlaceOn}
              disabled={!owner}
            />
          </div>
        </SettingRow>
        <SettingRow
          title="Returning visitor"
          description="Someone back on a link after a while. Added to the visit's alert, as above. Only visits still kept are remembered (retention)."
        >
          <div className="alert-type__fields">
            <Field
              label="Away more than (days)"
              value={afterDays}
              onChange={setAfterDays}
              inputMode="numeric"
              maxLength={2}
              disabled={!owner}
              error={daysError}
            />
            <Switch
              label="Say when a visitor returns"
              hideLabel
              checked={returningOn}
              onChange={setReturningOn}
              disabled={!owner}
            />
          </div>
        </SettingRow>
        {error !== null && <ErrorNotice error={error} />}
        <div>
          <Button
            variant="primary"
            type="submit"
            busy={busy}
            busyLabel="Saving…"
            disabled={owner && (!changed || !valid)}
            disabledReason={owner ? null : OWNER_ONLY}
          >
            Save alert types
          </Button>
        </div>
      </form>
    </Card>
  );
}

/** What each queued row is, for the delivery log's "What" column (DESIGN §16 M7.5). */
const KIND_LABEL: Readonly<Record<Delivery['kind'], string>> = {
  'telegram.visit_alert': 'Visit alert',
  'telegram.password_reset': 'Password reset',
  'telegram.health_alert': 'Health alert',
  'telegram.test': 'Test',
  'telegram.digest': 'Daily digest',
  'telegram.spike': 'Volume spike',
  'telegram.new_place': 'New place',
  'telegram.returning': 'Returning',
};

function statusBadge(d: Delivery): React.JSX.Element {
  switch (d.status) {
    case 'done':
      return <Badge tone="ok">Delivered</Badge>;
    case 'failed':
      return (
        <Badge tone="warn">
          Retrying ({count(d.attempts)}/{count(d.max_attempts)})
        </Badge>
      );
    case 'dead':
      return <Badge tone="error">Dead-lettered</Badge>;
    case 'in_flight':
      return <Badge tone="info">Sending</Badge>;
    case 'pending':
      return <Badge>Waiting</Badge>;
  }
}

function DeliveryLog(): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const status = search.get('status') ?? '';
  const cursor = search.get('before');
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (status !== '') params.set('status', status);
  if (cursor !== null) params.set('cursor', cursor);
  const query = useApi(OUTBOX, params, outboxSchema, { refetchInterval: POLL_MS });
  const [retrying, setRetrying] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const update = (next: Record<string, string | null>): void => {
    const copy = new URLSearchParams(search);
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === '') copy.delete(key);
      else copy.set(key, value);
    }
    setSearch(copy, { replace: true });
  };

  async function retry(d: Delivery): Promise<void> {
    setRetrying(d.id);
    setError(null);
    const result = await retryDelivery(me.csrf_token, d.id);
    setRetrying(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Requeued. It is sent on the next attempt.');
    void client.invalidateQueries({ queryKey: [OUTBOX] });
  }

  const updated = query.dataUpdatedAt > 0 ? new Date(query.dataUpdatedAt).toISOString() : null;

  return (
    <Panel
      query={query}
      kind="table"
      title="Delivery log"
      description="Every alert the queue has handled, newest first. A dead letter stays until it is retried."
      actions={<Freshness iso={updated} zone={me.timezone} />}
      // Empty only when nothing was ever queued. A filter with no matches is answered inside
      // the log, with the filter still there to change (an empty panel had no way back).
      isEmpty={(d) => status === '' && cursor === null && d.items.length === 0}
      empty="Nothing sent yet. An alert is queued when a person visits a link."
    >
      {(outbox) => (
        <div className="stack delivery-log">
          <StatStrip
            label="Queue"
            items={[
              { key: 'pending', label: 'Waiting', value: count(outbox.counts.pending) },
              { key: 'held', label: 'Held by quiet hours', value: count(outbox.counts.held) },
              { key: 'failed', label: 'Retrying', value: count(outbox.counts.failed) },
              { key: 'dead', label: 'Dead-lettered', value: count(outbox.counts.dead) },
            ]}
          />
          <SegmentedControl
            label="Status"
            value={status}
            options={FILTERS}
            onChange={(value) => {
              update({ status: value, before: null });
            }}
          />
          {error !== null && <ErrorNotice error={error} />}
          {outbox.items.length === 0 ? (
            <EmptyState
              title="No deliveries with this status"
              reason={
                status === 'dead'
                  ? 'Nothing has failed for good. A dead letter appears here until it is retried.'
                  : 'Choose another status, or show every delivery.'
              }
              action={
                <Button
                  variant="secondary"
                  onClick={() => {
                    update({ status: null, before: null });
                  }}
                >
                  Show all deliveries
                </Button>
              }
            />
          ) : (
            <DataTable
              caption="Deliveries"
              rowKey={(d) => String(d.id)}
              rows={outbox.items}
              columns={[
                {
                  key: 'time',
                  header: 'Queued',
                  render: (d) => <Timestamp iso={d.created_at} zone={me.timezone} />,
                },
                { key: 'what', header: 'What', render: (d) => KIND_LABEL[d.kind] },
                {
                  key: 'visit',
                  header: 'Visit',
                  render: (d) =>
                    d.visit_id === null ? (
                      <span className="muted">—</span>
                    ) : (
                      <Link className="link t-mono" to={`/visits/${d.visit_id}`} title={d.visit_id}>
                        {d.visit_id.slice(0, 8)}…
                      </Link>
                    ),
                },
                { key: 'link', header: 'Link', render: (d) => d.link_label ?? '—' },
                {
                  key: 'geofence',
                  header: 'Geofence',
                  render: (d) =>
                    d.geofence_name ??
                    (d.geofence_state === 'undetermined' ? (
                      <span className="muted">Not confirmed</span>
                    ) : (
                      <span className="muted">—</span>
                    )),
                },
                {
                  key: 'priority',
                  header: 'Priority',
                  render: (d) => (
                    <Badge {...toned(PRIORITY[d.priority].tone)}>
                      {PRIORITY[d.priority].label}
                    </Badge>
                  ),
                },
                {
                  key: 'status',
                  header: 'Status',
                  render: (d) => (
                    <span className="link-cell">
                      {statusBadge(d)}
                      {d.last_error !== null && d.status !== 'done' && (
                        <span className="t-meta" title={d.last_error}>
                          {d.last_error.length > 60
                            ? `${d.last_error.slice(0, 59)}…`
                            : d.last_error}
                        </span>
                      )}
                    </span>
                  ),
                },
                {
                  key: 'action',
                  header: <span className="sr-only">Actions</span>,
                  render: (d) =>
                    d.status === 'dead' ? (
                      <Button
                        size="sm"
                        icon="Retry"
                        busy={retrying === d.id}
                        busyLabel="Retrying…"
                        disabledReason={owner ? null : OWNER_ONLY}
                        onClick={() => {
                          void retry(d);
                        }}
                      >
                        Retry
                      </Button>
                    ) : null,
                },
              ]}
            />
          )}
          <div className="row">
            {cursor !== null && (
              <Button
                variant="ghost"
                onClick={() => {
                  update({ before: null });
                }}
              >
                Newest
              </Button>
            )}
            {outbox.next_cursor !== null && (
              <Button
                variant="ghost"
                onClick={() => {
                  update({ before: String(outbox.next_cursor) });
                }}
              >
                Older
              </Button>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
