/**
 * The label form: where a visit really was (DESIGN §16 M8, F4.AC15).
 *
 * Shared by the labelling queue and the visit page's "Label this visit" dialog, so the two
 * can never ask different questions. States come from the GeoNames region list and cities
 * from its place list -- the spellings the engine names places with, so a right answer
 * matches -- and a town the list lacks can still be typed. Coordinates are never typed: the
 * only way to give some is to copy the visit's own consented GPS fix.
 *
 * Owner-only (CLAUDE.md invariant 9): an analyst sees the form disabled, with the reason.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import type { ApiError } from '@/api/client';
import { placesSchema, regionsSchema } from '@/api/geofences';
import {
  CONNECTION_KINDS,
  CONNECTION_LABEL,
  NETWORKS,
  NETWORK_LABEL,
  createLabel,
  refreshAccuracy,
  updateLabel,
  type LabelFields,
} from '@/api/groundtruth';
import { useApi } from '@/api/query';
import type { GroundTruthLabel } from '@/api/schemas';
import {
  Alert,
  Button,
  Checkbox,
  ErrorNotice,
  Field,
  SegmentedControl,
  Select,
  toast,
} from '@/components/ui';
import { countryName } from '@/format';
import { asConnection, asNetwork, OWNER_ONLY_LABEL, type Carry, type GpsPlace } from './figures';
import { useSession } from '@/session';

const FENCES = '/api/v1/geofences';
const OTHER_TOWN = '__other';
function blank(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

export function LabelForm({
  visitId,
  existing,
  gps,
  carry,
  onSaved,
  extraActions,
}: {
  readonly visitId: string;
  readonly existing: GroundTruthLabel | null;
  /** The visit's consented GPS place, to pre-fill from and offer to copy. */
  readonly gps: GpsPlace | null;
  readonly carry: Carry | null;
  readonly onSaved: (label: GroundTruthLabel) => void;
  /** The queue's Skip, beside Save and Can't tell. */
  readonly extraActions?: React.ReactNode;
}): React.JSX.Element {
  const { me } = useSession();
  const owner = me.role === 'owner';
  const client = useQueryClient();
  const regions = useApi(`${FENCES}/regions`, null, regionsSchema);

  const start = existing?.truth ?? (existing === null ? gps : null);
  const [country, setCountry] = useState(start?.country_code ?? 'IN');
  const [admin1, setAdmin1] = useState(start?.admin1 ?? '');
  const [admin2, setAdmin2] = useState(start?.admin2 ?? '');
  const [city, setCity] = useState(start?.city ?? '');
  const [typedTown, setTypedTown] = useState(false);
  const [useGps, setUseGps] = useState(existing === null ? gps !== null : existing.has_coordinates);
  const [connection, setConnection] = useState<string>(
    existing?.connection_kind ?? carry?.connection_kind ?? '',
  );
  const [vpn, setVpn] = useState<string>(
    vpnValue(existing === null ? (carry?.vpn_used ?? null) : existing.vpn_used),
  );
  const [network, setNetwork] = useState<string>(existing?.network ?? carry?.network ?? '');
  const [notes, setNotes] = useState(existing?.notes ?? '');
  const [busy, setBusy] = useState<'save' | 'cant' | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const places = useApi(`${FENCES}/places`, new URLSearchParams({ country }), placesSchema, {
    enabled: country !== '',
  });
  const towns = useMemo(
    () =>
      (places.data?.places ?? [])
        .filter((p) => admin1 !== '' && p.admin1 === admin1)
        .map((p) => p.name),
    [places.data, admin1],
  );
  const listed = city === '' || towns.includes(city);
  const showTyped = typedTown || !listed || places.isError;

  const countries = useMemo(
    () =>
      (regions.data?.countries ?? [])
        .map((c) => ({ value: c.key, label: countryName(c.key) }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [regions.data],
  );
  const states = useMemo(
    () => (regions.data?.divisions ?? []).filter((d) => d.country === country),
    [regions.data, country],
  );

  const placeError =
    admin1 === '' && (blank(city) !== null || blank(admin2) !== null)
      ? 'Say the state first: a city is matched within its state.'
      : null;

  async function send(cantTell: boolean): Promise<void> {
    setBusy(cantTell ? 'cant' : 'save');
    setError(null);
    const fields: LabelFields = {
      cant_tell: cantTell,
      country_code: cantTell ? null : country,
      admin1: cantTell ? null : blank(admin1),
      admin2: cantTell ? null : blank(admin2),
      city: cantTell ? null : blank(city),
      use_gps: cantTell ? null : gps === null ? null : useGps,
      connection_kind: asConnection(connection === '' ? null : connection),
      vpn_used: vpn === 'on' ? true : vpn === 'off' ? false : null,
      network: asNetwork(network === '' ? null : network),
      notes: blank(notes),
    };
    const result =
      existing === null
        ? await createLabel(me.csrf_token, visitId, fields)
        : await updateLabel(me.csrf_token, existing.id, fields);
    setBusy(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(cantTell ? 'Recorded as can’t tell.' : existing === null ? 'Labelled.' : 'Label saved.');
    refreshAccuracy(client, visitId);
    onSaved(result.data);
  }

  const disabled = !owner || busy !== null;

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        if (placeError === null && owner) void send(false);
      }}
    >
      {!owner && <Alert tone="info">{OWNER_ONLY_LABEL}</Alert>}
      {gps !== null && existing === null && (
        <Alert tone="info">Pre-filled from this visit’s GPS fix. Check it before saving.</Alert>
      )}
      <div className="form-grid">
        <Select
          label="Country"
          hideLabel={false}
          block
          value={country}
          onChange={(value) => {
            setCountry(value);
            setAdmin1('');
            setCity('');
          }}
          options={
            countries.length > 0 ? countries : [{ value: country, label: countryName(country) }]
          }
        />
        <Select
          label="State"
          hideLabel={false}
          block
          value={admin1}
          onChange={(value) => {
            setAdmin1(value);
            setCity('');
            setTypedTown(false);
          }}
          options={[
            { value: '', label: 'Not known' },
            ...states.map((d) => ({ value: d.name, label: d.name })),
            ...(admin1 !== '' && !states.some((d) => d.name === admin1)
              ? [{ value: admin1, label: admin1 }]
              : []),
          ]}
        />
        {showTyped ? (
          <Field
            label="City or town"
            required={false}
            value={city}
            onChange={setCity}
            maxLength={100}
            hint={
              places.isError
                ? 'The place list is not installed; type the name.'
                : 'As GeoNames spells it.'
            }
            error={placeError}
            disabled={disabled}
          />
        ) : (
          <Select
            label="City or town"
            hideLabel={false}
            block
            value={city}
            onChange={(value) => {
              if (value === OTHER_TOWN) {
                setTypedTown(true);
                setCity('');
              } else {
                setCity(value);
              }
            }}
            options={[
              { value: '', label: admin1 === '' ? 'Choose a state first' : 'Not known' },
              ...towns.map((t) => ({ value: t, label: t })),
              ...(admin1 === '' ? [] : [{ value: OTHER_TOWN, label: 'Another town…' }]),
            ]}
          />
        )}
        <Field
          label="District (optional)"
          required={false}
          value={admin2}
          onChange={setAdmin2}
          maxLength={100}
          disabled={disabled}
        />
      </div>
      {gps !== null && (
        <Checkbox
          label="Keep this visit’s GPS fix as the label’s coordinates"
          checked={useGps}
          onChange={setUseGps}
          disabled={disabled}
        />
      )}
      <div className="stack">
        <Labelled label="Connection">
          <SegmentedControl
            label="Connection"
            value={connection}
            onChange={setConnection}
            options={[
              ...CONNECTION_KINDS.map((k) => ({ value: k, label: CONNECTION_LABEL[k] })),
              { value: '', label: 'Not said' },
            ]}
          />
        </Labelled>
        <Labelled label="VPN">
          <SegmentedControl
            label="VPN"
            value={vpn}
            onChange={setVpn}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'on', label: 'On' },
              { value: '', label: 'Not said' },
            ]}
          />
        </Labelled>
        <Select
          label="Network (under any VPN)"
          hideLabel={false}
          block
          value={network}
          onChange={setNetwork}
          options={[
            { value: '', label: 'Not said' },
            ...NETWORKS.map((n) => ({ value: n, label: NETWORK_LABEL[n] })),
          ]}
        />
      </div>
      <Field
        label="Notes (optional)"
        required={false}
        value={notes}
        onChange={setNotes}
        maxLength={500}
        placeholder="Phone, at home"
        disabled={disabled}
      />
      {error !== null && <ErrorNotice error={error} />}
      <div className="row-actions">
        <Button
          type="submit"
          variant="primary"
          busy={busy === 'save'}
          busyLabel="Saving…"
          disabled={disabled || placeError !== null}
          disabledReason={owner ? null : OWNER_ONLY_LABEL}
        >
          {existing === null ? 'Save label' : 'Save changes'}
        </Button>
        <Button
          variant="secondary"
          busy={busy === 'cant'}
          busyLabel="Saving…"
          disabled={disabled}
          disabledReason={owner ? null : OWNER_ONLY_LABEL}
          onClick={() => {
            void send(true);
          }}
        >
          Can’t tell
        </Button>
        {extraActions}
      </div>
    </form>
  );
}

/** A visible label over a control that names itself only to assistive technology. */
function Labelled({
  label,
  children,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="field">
      <span className="field__label" aria-hidden="true">
        {label}
      </span>
      {children}
    </div>
  );
}

function vpnValue(used: boolean | null): string {
  return used === true ? 'on' : used === false ? 'off' : '';
}
