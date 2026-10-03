/**
 * The filter toolbar and the period picker (DESIGN §5.3, §9.3).
 *
 * Every control writes the URL through `filters.ts`, exactly as the M5 filter bar did
 * (F9.AC13): a shared link reproduces the view, and every change is a history entry. Text
 * filters apply on Enter or Apply, not per keystroke, so typing an ASN is one request.
 */

import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { useApi } from '@/api/query';
import { linkChoicesSchema } from '@/api/schemas';
import { Icon } from '@/components/icons';
import {
  FILTER_DEFS,
  chipValue,
  clearAll,
  clearDimension,
  isActive,
  setScalar,
  toggleListValue,
  type FilterDef,
} from '@/components/shell/filterDefs';
import { Button, Checkbox, CopyButton, Input, MenuItem, Popover, Select } from '@/components/ui';
import {
  PRESETS,
  PRESET_LABEL,
  localDate,
  parseFilters,
  serializeFilters,
  type Filters,
} from '@/filters';
import { label } from '@/format';

function useFilterState(): { readonly filters: Filters; readonly write: (next: Filters) => void } {
  const [search, setSearch] = useSearchParams();
  return {
    filters: parseFilters(search),
    write: (next) => {
      setSearch(serializeFilters(next));
    },
  };
}

// ---------------------------------------------------------------------------
// The toolbar
// ---------------------------------------------------------------------------

export function FilterToolbar(): React.JSX.Element {
  const { filters, write } = useFilterState();
  const active = FILTER_DEFS.filter((d) => isActive(d, filters));
  const anyActive = active.length > 0 || filters.scalars.link_id !== undefined;

  return (
    <div className="toolbar" role="group" aria-label="Filters">
      <LinkSelect
        value={filters.scalars.link_id ?? ''}
        onChange={(v) => {
          write(setScalar(filters, 'link_id', v));
        }}
      />
      <AddFilter filters={filters} write={write} />
      {active.map((def) => (
        <Chip key={def.id} def={def} filters={filters} write={write} />
      ))}
      {anyActive && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            write(clearAll(filters));
          }}
        >
          Clear all
        </Button>
      )}
      <span className="toolbar__end">
        <CopyButton value={window.location.href} label="Copy link to this view" />
      </span>
    </div>
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
    <span className="toolbar__link">
      <Select
        label="Link"
        size="sm"
        value={value}
        onChange={onChange}
        options={[
          { value: '', label: 'All links' },
          ...(links.data ?? []).map((link) => ({
            value: link.id,
            label: `${link.slug} — ${link.label}${link.archived_at === null ? '' : ' (archived)'}`,
          })),
        ]}
      />
      {links.isError && (
        <span className="field__error" role="alert">
          Links could not be loaded.
        </span>
      )}
    </span>
  );
}

/** "＋ Filter": choose a dimension, then edit it, in one popover. */
function AddFilter({
  filters,
  write,
}: {
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
}): React.JSX.Element {
  return (
    <Popover
      label="Add a filter"
      className="pop--wide"
      trigger={({ ref, ...props }) => (
        <button type="button" ref={ref} className="btn btn--sm btn--ghost" {...props}>
          <Icon.Add size={14} strokeWidth={1.75} aria-hidden="true" />
          Filter
        </button>
      )}
    >
      {(close) => <AddFilterBody filters={filters} write={write} close={close} />}
    </Popover>
  );
}

function AddFilterBody({
  filters,
  write,
  close,
}: {
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
  readonly close: () => void;
}): React.JSX.Element {
  const [chosen, setChosen] = useState<FilterDef | null>(null);
  if (chosen !== null) {
    return (
      <div className="filter-editor">
        <button
          type="button"
          className="menu__item"
          onClick={() => {
            setChosen(null);
          }}
        >
          <Icon.Previous size={16} strokeWidth={1.75} aria-hidden="true" />
          <span>{chosen.label}</span>
        </button>
        <Editor def={chosen} filters={filters} write={write} close={close} />
      </div>
    );
  }
  return (
    <div className="menu">
      {FILTER_DEFS.map((def) => (
        <MenuItem
          key={def.id}
          hint={isActive(def, filters) ? chipValue(def, filters) : undefined}
          onSelect={() => {
            setChosen(def);
          }}
        >
          {def.label}
        </MenuItem>
      ))}
    </div>
  );
}

/** An active filter: click to edit, ✕ to remove. */
function Chip({
  def,
  filters,
  write,
}: {
  readonly def: FilterDef;
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
}): React.JSX.Element {
  const value = chipValue(def, filters);
  return (
    <span className="chip">
      <Popover
        label={`Edit ${def.label.toLowerCase()} filter`}
        className="pop--wide pop--padded"
        trigger={({ ref, ...props }) => (
          <button type="button" ref={ref} className="chip__body" {...props}>
            <span className="chip__name">{def.label}:</span> {value}
          </button>
        )}
      >
        {(close) => <Editor def={def} filters={filters} write={write} close={close} />}
      </Popover>
      <button
        type="button"
        className="chip__remove"
        aria-label={`Remove ${def.label.toLowerCase()} filter`}
        onClick={() => {
          write(clearDimension(def, filters));
        }}
      >
        <Icon.Close size={12} strokeWidth={2} aria-hidden="true" />
      </button>
    </span>
  );
}

function Editor({
  def,
  filters,
  write,
  close,
}: {
  readonly def: FilterDef;
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
  readonly close: () => void;
}): React.JSX.Element {
  switch (def.kind) {
    case 'list':
      return (
        <fieldset className="filter-editor__options">
          <legend className="sr-only">{def.label}</legend>
          {def.options.map((option) => (
            <Checkbox
              key={option}
              label={label(option)}
              checked={filters.lists[def.key].includes(option)}
              onChange={() => {
                write(toggleListValue(filters, def.key, option));
              }}
            />
          ))}
        </fieldset>
      );
    case 'choice':
      return (
        <div className="menu" role="group" aria-label={def.label}>
          {[['', 'Any'] as const, ...def.options].map(([value, text]) => (
            <MenuItem
              key={value}
              checked={(filters.scalars[def.key] ?? '') === value}
              onSelect={() => {
                write(setScalar(filters, def.key, value));
                close();
              }}
            >
              {text}
            </MenuItem>
          ))}
        </div>
      );
    case 'text':
      return <TextEditor def={def} filters={filters} write={write} close={close} />;
  }
}

function TextEditor({
  def,
  filters,
  write,
  close,
}: {
  readonly def: Extract<FilterDef, { kind: 'text' }>;
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
  readonly close: () => void;
}): React.JSX.Element {
  const [draft, setDraft] = useState(filters.scalars[def.key] ?? '');
  const apply = (): void => {
    const value = (def.normalise ?? ((v: string) => v))(draft.trim());
    write(setScalar(filters, def.key, value));
    close();
  };
  return (
    <form
      className="filter-editor__text"
      onSubmit={(event) => {
        event.preventDefault();
        apply();
      }}
    >
      <Input
        size="sm"
        aria-label={def.label}
        value={draft}
        placeholder={def.placeholder}
        inputMode={def.inputMode}
        maxLength={def.maxLength}
        data-autofocus
        autoFocus
        onChange={(event) => {
          setDraft(event.target.value);
        }}
      />
      {def.hint !== undefined && <p className="field__hint">{def.hint}</p>}
      <Button type="submit" variant="primary" size="sm">
        Apply
      </Button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// The period picker
// ---------------------------------------------------------------------------

/** The period, beside the page title (DESIGN §9.3); writes the same `range`/`from`/`to`. */
export function PeriodPicker({ zone }: { readonly zone: string }): React.JSX.Element {
  const { filters, write } = useFilterState();
  const shown =
    filters.range === 'custom' && filters.from !== null && filters.to !== null
      ? `${short(filters.from)} – ${short(filters.to)}`
      : PRESET_LABEL[filters.range === 'custom' ? '30d' : filters.range];
  return (
    <Popover
      label="Period"
      align="end"
      className="pop--wide"
      trigger={({ ref, ...props }) => (
        <button type="button" ref={ref} className="btn" {...props}>
          <Icon.Calendar size={16} strokeWidth={1.75} aria-hidden="true" />
          {shown}
          <Icon.Chevron size={14} strokeWidth={1.75} aria-hidden="true" />
        </button>
      )}
    >
      {(close) => <PeriodBody filters={filters} write={write} zone={zone} close={close} />}
    </Popover>
  );
}

function short(date: string): string {
  return new Date(`${date}T00:00:00`).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
  });
}

function PeriodBody({
  filters,
  write,
  zone,
  close,
}: {
  readonly filters: Filters;
  readonly write: (next: Filters) => void;
  readonly zone: string;
  readonly close: () => void;
}): React.JSX.Element {
  const today = localDate(Date.now(), zone);
  const [from, setFrom] = useState(filters.from ?? today);
  const [to, setTo] = useState(filters.to ?? today);
  return (
    <div className="period">
      <div className="menu">
        {PRESETS.map((p) => (
          <MenuItem
            key={p}
            checked={filters.range === p}
            onSelect={() => {
              write({ ...filters, range: p, from: null, to: null });
              close();
            }}
          >
            {PRESET_LABEL[p]}
          </MenuItem>
        ))}
      </div>
      <form
        className="period__custom"
        onSubmit={(event) => {
          event.preventDefault();
          write({ ...filters, range: 'custom', from, to });
          close();
        }}
      >
        <span className="t-meta">Custom range</span>
        <div className="period__dates">
          <Input
            type="date"
            size="sm"
            aria-label="From"
            value={from}
            max={today}
            onChange={(event) => {
              setFrom(event.target.value);
            }}
          />
          <Input
            type="date"
            size="sm"
            aria-label="To"
            value={to}
            max={today}
            onChange={(event) => {
              setTo(event.target.value);
            }}
          />
        </div>
        <Button type="submit" size="sm">
          Apply range
        </Button>
        <span className="t-meta">Days are local to {zone}.</span>
      </form>
    </div>
  );
}
