/**
 * The filter bar: every F9.AC13 filter, written straight to the URL.
 *
 * All controls are native form elements, so keyboard operation and screen-reader
 * labelling come from the browser rather than from code that could get them wrong
 * (NFR7.AC2). Text filters apply on Enter or when focus leaves the field, not on every
 * keystroke, so typing an ASN is one request rather than five.
 */

import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import { linkChoicesSchema } from '@/api/schemas';
import {
  PRESETS,
  PRESET_LABEL,
  activeCount,
  localDate,
  parseFilters,
  serializeFilters,
  type Filters,
  type ListKey,
  type ScalarKey,
} from '@/filters';
import { label } from '@/format';

const CLASSIFICATIONS = ['human', 'unknown', 'bot', 'crawler', 'datacenter', 'spam', 'spoofed'];
const DEVICES = ['mobile', 'tablet', 'desktop', 'tv', 'bot', 'unknown'];
const CONNECTIONS = [
  'broadband',
  'mobile',
  'business',
  'datacenter',
  'vpn_suspected',
  'tor',
  'unknown',
];
const CONSENT = ['granted', 'denied', 'unavailable', 'not_asked', 'blocked_by_webview'];

export function FilterBar({ zone }: { readonly zone: string }): React.JSX.Element {
  const [search, setSearch] = useSearchParams();
  const filters = parseFilters(search);
  const [copied, setCopied] = useState(false);

  const write = (next: Filters): void => {
    setSearch(serializeFilters(next));
  };
  const setScalar = (key: ScalarKey, value: string): void => {
    const scalars = Object.fromEntries(
      Object.entries({ ...filters.scalars, [key]: value }).filter(([, v]) => v !== ''),
    );
    write({ ...filters, scalars });
  };
  const toggle = (key: ListKey, value: string): void => {
    const current = filters.lists[key];
    const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
    write({ ...filters, lists: { ...filters.lists, [key]: next } });
  };

  const active = activeCount(filters);
  const today = localDate(Date.now(), zone);

  return (
    <form
      className="filter-bar"
      aria-label="Filters"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      <div className="filter-row">
        <label className="control">
          <span>Period</span>
          <select
            value={filters.range}
            onChange={(event) => {
              const range = event.target.value;
              if (range === 'custom') {
                write({
                  ...filters,
                  range: 'custom',
                  from: filters.from ?? today,
                  to: filters.to ?? today,
                });
              } else {
                write({ ...filters, range: range as Filters['range'], from: null, to: null });
              }
            }}
          >
            {PRESETS.map((p) => (
              <option key={p} value={p}>
                {PRESET_LABEL[p]}
              </option>
            ))}
            <option value="custom">Custom dates</option>
          </select>
        </label>
        {filters.range === 'custom' && (
          <>
            <label className="control">
              <span>From</span>
              <input
                type="date"
                value={filters.from ?? today}
                max={today}
                onChange={(event) => {
                  write({ ...filters, from: event.target.value });
                }}
              />
            </label>
            <label className="control">
              <span>To</span>
              <input
                type="date"
                value={filters.to ?? today}
                max={today}
                onChange={(event) => {
                  write({ ...filters, to: event.target.value });
                }}
              />
            </label>
          </>
        )}
        <LinkSelect
          value={filters.scalars.link_id ?? ''}
          onChange={(v) => {
            setScalar('link_id', v);
          }}
        />
        <TextFilter
          label="Search"
          value={filters.scalars.search ?? ''}
          placeholder="ISP, city, browser, link…"
          onCommit={(v) => {
            setScalar('search', v);
          }}
        />
        <label className="control checkbox-control">
          <input
            type="checkbox"
            checked={filters.scalars.include_automated === 'true'}
            onChange={(event) => {
              setScalar('include_automated', event.target.checked ? 'true' : '');
            }}
          />
          <span>Include automated traffic</span>
        </label>
      </div>

      <details className="filter-more">
        <summary>More filters{active > 0 ? ` (${String(active)} active)` : ''}</summary>
        <div className="filter-grid">
          <CheckGroup
            legend="Classification"
            options={CLASSIFICATIONS}
            selected={filters.lists.classification}
            onToggle={(v) => {
              toggle('classification', v);
            }}
          />
          <CheckGroup
            legend="Device class"
            options={DEVICES}
            selected={filters.lists.device_class}
            onToggle={(v) => {
              toggle('device_class', v);
            }}
          />
          <CheckGroup
            legend="Connection"
            options={CONNECTIONS}
            selected={filters.lists.connection_class}
            onToggle={(v) => {
              toggle('connection_class', v);
            }}
          />
          <fieldset className="control-group">
            <legend>Location (strict only)</legend>
            <TextFilter
              label="Country code"
              value={filters.scalars.country_code ?? ''}
              placeholder="IN"
              maxLength={2}
              onCommit={(v) => {
                setScalar('country_code', v.toUpperCase());
              }}
            />
            <TextFilter
              label="State"
              value={filters.scalars.admin1 ?? ''}
              placeholder="Karnataka"
              onCommit={(v) => {
                setScalar('admin1', v);
              }}
            />
            <TextFilter
              label="City"
              value={filters.scalars.city ?? ''}
              placeholder="Bengaluru"
              onCommit={(v) => {
                setScalar('city', v);
              }}
            />
            <Choice
              label="Coordinates"
              value={filters.scalars.has_gps ?? ''}
              options={[
                ['', 'Any'],
                ['true', 'Consented GPS'],
                ['false', 'No GPS'],
              ]}
              onChange={(v) => {
                setScalar('has_gps', v);
              }}
            />
          </fieldset>
          <fieldset className="control-group">
            <legend>Network and identity</legend>
            <TextFilter
              label="ASN"
              value={filters.scalars.asn ?? ''}
              placeholder="55836"
              inputMode="numeric"
              onCommit={(v) => {
                setScalar('asn', v.replace(/^AS/i, ''));
              }}
            />
            <Choice
              label="Proxy suspected"
              value={filters.scalars.is_proxy_suspected ?? ''}
              options={[
                ['', 'Any'],
                ['true', 'Yes'],
                ['false', 'No'],
              ]}
              onChange={(v) => {
                setScalar('is_proxy_suspected', v);
              }}
            />
            <TextFilter
              label="Visitor ID"
              value={filters.scalars.visitor_id ?? ''}
              placeholder="32 hex characters"
              onCommit={(v) => {
                setScalar('visitor_id', v.toLowerCase());
              }}
            />
            <Choice
              label="Consent"
              value={filters.scalars.consent_state ?? ''}
              options={[['', 'Any'], ...CONSENT.map((c): [string, string] => [c, label(c)])]}
              onChange={(v) => {
                setScalar('consent_state', v);
              }}
            />
          </fieldset>
          <fieldset className="control-group">
            <legend>Minimum confidence</legend>
            <TextFilter
              label="State (0–1)"
              value={filters.scalars.min_confidence_admin1 ?? ''}
              placeholder="0.8"
              inputMode="decimal"
              onCommit={(v) => {
                setScalar('min_confidence_admin1', v);
              }}
            />
            <TextFilter
              label="City (0–1)"
              value={filters.scalars.min_confidence_city ?? ''}
              placeholder="0.9"
              inputMode="decimal"
              onCommit={(v) => {
                setScalar('min_confidence_city', v);
              }}
            />
          </fieldset>
        </div>
      </details>

      <div className="filter-actions">
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(window.location.href).then(() => {
              setCopied(true);
              window.setTimeout(() => {
                setCopied(false);
              }, 2000);
            });
          }}
        >
          {copied ? 'Link copied' : 'Copy link to this view'}
        </button>
        {active > 0 && (
          <button
            type="button"
            className="link"
            onClick={() => {
              write({
                ...filters,
                scalars: {},
                lists: { classification: [], device_class: [], connection_class: [] },
              });
            }}
          >
            Clear filters
          </button>
        )}
        <span className="sr-only" role="status">
          {copied ? 'Link copied to the clipboard' : ''}
        </span>
      </div>
    </form>
  );
}

function LinkSelect({
  value,
  onChange,
}: {
  readonly value: string;
  readonly onChange: (value: string) => void;
}): React.JSX.Element {
  const links = useApi('/api/v1/links', null, linkChoicesSchema);
  return (
    <label className="control">
      <span>Link</span>
      <select
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        aria-describedby={links.isError ? 'link-select-error' : undefined}
      >
        <option value="">All links</option>
        {links.data?.map((link) => (
          <option key={link.id} value={link.id}>
            {link.slug} — {link.label}
            {link.archived_at === null ? '' : ' (archived)'}
          </option>
        ))}
      </select>
      {links.isError && (
        <span id="link-select-error" className="error-text small">
          Links could not be loaded.
        </span>
      )}
    </label>
  );
}

function TextFilter({
  label: text,
  value,
  placeholder,
  maxLength,
  inputMode,
  onCommit,
}: {
  readonly label: string;
  readonly value: string;
  readonly placeholder?: string;
  readonly maxLength?: number;
  readonly inputMode?: 'numeric' | 'decimal' | 'text';
  readonly onCommit: (value: string) => void;
}): React.JSX.Element {
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    setDraft(value);
  }, [value]);
  const commit = (): void => {
    if (draft.trim() !== value) onCommit(draft.trim());
  };
  return (
    <label className="control">
      <span>{text}</span>
      <input
        type="text"
        value={draft}
        placeholder={placeholder}
        maxLength={maxLength}
        inputMode={inputMode}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit();
        }}
      />
    </label>
  );
}

function Choice({
  label: text,
  value,
  options,
  onChange,
}: {
  readonly label: string;
  readonly value: string;
  readonly options: readonly (readonly [string, string])[];
  readonly onChange: (value: string) => void;
}): React.JSX.Element {
  return (
    <label className="control">
      <span>{text}</span>
      <select
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function CheckGroup({
  legend,
  options,
  selected,
  onToggle,
}: {
  readonly legend: ReactNode;
  readonly options: readonly string[];
  readonly selected: readonly string[];
  readonly onToggle: (value: string) => void;
}): React.JSX.Element {
  return (
    <fieldset className="control-group">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option} className="checkbox">
          <input
            type="checkbox"
            checked={selected.includes(option)}
            onChange={() => {
              onToggle(option);
            }}
          />
          {label(option)}
        </label>
      ))}
    </fieldset>
  );
}
