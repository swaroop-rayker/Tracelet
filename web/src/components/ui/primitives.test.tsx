/**
 * The primitives' states and accessibility contracts (DESIGN §5, §8), rendered to static
 * markup in Node like the Panel tests: what is checked is the text a person reads and the
 * attributes assistive technology reads. Interaction (focus, arrow keys, dialogs) is
 * verified in the browser and recorded in the phase notes, not simulated here.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Checklist,
  Dialog,
  EmptyState,
  ErrorNotice,
  Field,
  IconButton,
  Identifier,
  Loading,
  RankedList,
  Secret,
  SegmentedControl,
  SettingRow,
  Sparkline,
  Stat,
  Switch,
} from '@/components/ui';
import { currentToasts, dismissToast, toast } from '@/components/ui/toast';
import { middleTruncate, relativeTime } from '@/components/ui/util';
import type { ApiError } from '@/api/client';

function html(node: React.JSX.Element): string {
  return renderToStaticMarkup(node);
}

function textOf(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

describe('Button', () => {
  it('is a real button with a type, and says it is working while busy', () => {
    const idle = html(<Button>Export CSV</Button>);
    expect(idle).toContain('type="button"');
    expect(textOf(idle)).toBe('Export CSV');

    const busy = html(
      <Button busy busyLabel="Exporting…">
        Export CSV
      </Button>,
    );
    expect(busy).toContain('aria-busy="true"');
    expect(busy).toContain('disabled=""');
    expect(textOf(busy)).toBe('Exporting…');
  });

  it('marks variants with classes, never inline styles (CSP)', () => {
    const danger = html(<Button variant="danger">Delete geofence</Button>);
    expect(danger).toContain('btn--danger');
    expect(danger).not.toContain('style=');
  });

  it('stays focusable when unavailable to this role, and says why (UI-17)', () => {
    const markup = html(
      <Button variant="primary" disabledReason="Only the owner can change geofences.">
        New geofence
      </Button>,
    );
    // aria-disabled, not disabled: a disabled button takes no focus, so its reason would be
    // out of reach of the keyboard.
    expect(markup).toContain('aria-disabled="true"');
    expect(markup).not.toContain('disabled=""');
    expect(markup).toContain('aria-describedby=');
    expect(markup).toContain('role="tooltip"');
    expect(textOf(markup)).toContain('Only the owner can change geofences.');
  });
});

describe('IconButton', () => {
  it('always has an accessible name, and its tooltip repeats it without being read twice', () => {
    const markup = html(<IconButton icon="Help" label="Help" />);
    expect(markup).toContain('aria-label="Help"');
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain('popover="manual"');
  });
});

describe('Field', () => {
  it('ties its hint and error to the input, and announces the error', () => {
    const markup = html(
      <Field
        label="Name"
        value=""
        onChange={() => undefined}
        hint="Shown in alerts."
        error="Already taken."
      />,
    );
    const input = /<input[^>]*>/.exec(markup)?.[0] ?? '';
    expect(input).toContain('aria-invalid="true"');
    const described = /aria-describedby="([^"]+)"/.exec(input)?.[1]?.split(' ') ?? [];
    expect(described).toHaveLength(2);
    for (const id of described) expect(markup).toContain(`id="${id}"`);
    expect(markup).toContain('role="alert"');
  });
});

describe('SegmentedControl', () => {
  it('is a radiogroup with one tab stop: the selected option', () => {
    const markup = html(
      <SegmentedControl
        label="Bucket"
        value="hour"
        onChange={() => undefined}
        options={[
          { value: 'day', label: 'Day' },
          { value: 'hour', label: 'Hour' },
        ]}
      />,
    );
    expect(markup).toContain('role="radiogroup"');
    expect(markup).toContain('aria-label="Bucket"');
    expect(markup.match(/tabindex="0"/g)).toHaveLength(1);
    expect(markup).toMatch(
      /aria-checked="true"[^>]*data-value="hour"|data-value="hour"[^>]*aria-checked="true"/,
    );
  });
});

describe('Checkbox and Switch', () => {
  it('are native checkboxes; a switch says it is one', () => {
    expect(
      html(<Checkbox label="Include automated" checked onChange={() => undefined} />),
    ).toContain('type="checkbox"');
    const sw = html(<Switch label="Active" checked={false} onChange={() => undefined} />);
    expect(sw).toContain('role="switch"');
    expect(textOf(sw)).toBe('Active');
  });
});

describe('Badge', () => {
  it('carries the word; the dot is decorative', () => {
    const markup = html(<Badge dot="bot">Bot</Badge>);
    expect(textOf(markup)).toBe('Bot');
    expect(markup).toContain('dot--bot');
    expect(markup).toContain('aria-hidden="true"');
  });
});

describe('Stat', () => {
  it('judges a change by goodness, not direction, and speaks the direction', () => {
    const markup = html(
      <Stat
        label="Automated share"
        value="9.8%"
        delta={{ direction: 'down', text: '4.5 pts', judgement: 'good' }}
        comparison="vs previous"
      />,
    );
    expect(markup).toContain('stat__delta--good');
    expect(textOf(markup)).toContain('down 4.5 pts');
  });

  it('shows a reason instead of a change when there is none, never a zero', () => {
    const markup = html(
      <Stat label="Geofence hit rate" value="—" note="Geofencing arrives in M6" />,
    );
    expect(textOf(markup)).toContain('Geofencing arrives in M6');
    expect(textOf(markup)).not.toContain('0');
  });

  it('keeps the exact figure for screen readers when the display is compact', () => {
    expect(textOf(html(<Stat label="Visits" value="128.4K" exact="128,421" />))).toContain(
      '128,421',
    );
  });
});

describe('RankedList', () => {
  const rows = [
    { key: 'MH', label: 'Maharashtra', count: 37, share: 0.5 },
    { key: 'KA', label: 'Karnataka', count: 20, share: 0.27 },
    { key: 'other', label: 'Other', count: 50, share: 0.23, muted: true },
  ];

  it('is a table with a caption, so it needs no separate data table (NFR7.AC3)', () => {
    const markup = html(<RankedList rows={rows} caption="Visits by state" labelHeader="State" />);
    expect(markup).toContain('<table');
    expect(markup).toContain('<caption class="sr-only">Visits by state</caption>');
    expect(textOf(markup)).toContain('Maharashtra 37 50.0%');
  });

  it('scales bars to the top named row; Other is capped at full width', () => {
    const markup = html(<RankedList rows={rows} caption="c" labelHeader="State" />);
    expect(markup).toContain('--w:100.0%');
    expect(markup).toContain('--w:54.1%');
    const widths = [...markup.matchAll(/--w:([0-9.]+)%/g)].map((m) => Number(m[1]));
    expect(widths).toEqual([100, 54.1, 100]);
  });

  it('makes selectable rows real buttons, and never the Other row', () => {
    const markup = html(
      <RankedList rows={rows} caption="c" labelHeader="State" onSelect={() => undefined} />,
    );
    expect(markup.match(/<button/g)).toHaveLength(2);
    expect(markup).toContain('aria-label="Filter to Maharashtra"');
  });
});

describe('Sparkline (E16)', () => {
  const trend = { values: [2, 5, null, 3], spoken: 'Over 4 days: low 2, high 5.' };

  it('is decorative SVG with no inline style, and says the same in words', () => {
    const markup = html(<Sparkline trend={trend} />);
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).not.toContain('style=');
    expect(textOf(markup)).toBe('Over 4 days: low 2, high 5.');
  });

  it('breaks the line where a value is missing, rather than inventing one', () => {
    const d = /<path d="([^"]+)"/.exec(html(<Sparkline trend={trend} />))?.[1] ?? '';
    expect(d.match(/M/g)?.length).toBe(2);
  });

  it('draws nothing from fewer than two known values', () => {
    expect(html(<Sparkline trend={{ values: [4, null], spoken: 'x' }} />)).toBe('');
  });

  it('sits under a Stat when the Stat is given a trend', () => {
    expect(html(<Stat label="Visits" value="12" trend={trend} />)).toContain('sparkline');
  });
});

describe('Settings primitives (DESIGN §10.8, E25, E26, E28)', () => {
  it('a checklist says met or not in words, not only by colour', () => {
    const text = textOf(
      html(
        <Checklist
          label="Password rules"
          items={[
            { key: 'a', text: 'At least 12 characters', met: true },
            { key: 'b', text: 'Repeated exactly', met: false },
          ]}
        />,
      ),
    );
    expect(text).toContain('At least 12 characters : met');
    expect(text).toContain('Repeated exactly : not yet');
  });

  it('a secret is shown in groups for typing', () => {
    expect(html(<Secret label="Secret" value="ABCDEFGHIJ" groups={4} />)).toContain('ABCD EFGH IJ');
    expect(html(<Secret label="Secret" value="ABCDEFGHIJ" />)).toContain('ABCDEFGHIJ');
  });

  it('a setting row names itself with an h3 under its card', () => {
    const markup = html(<SettingRow title="Password" description="At least 12." />);
    expect(markup).toContain('<h3 class="setting-row__title">Password</h3>');
  });

  it('a locked dialog offers no close button; an ordinary one does', () => {
    const noop = (): void => undefined;
    const locked = html(
      <Dialog open locked title="Your new recovery codes" onClose={noop}>
        codes
      </Dialog>,
    );
    const normal = html(
      <Dialog open title="Change password" onClose={noop}>
        form
      </Dialog>,
    );
    expect(locked).not.toContain('aria-label="Close"');
    expect(normal).toContain('aria-label="Close"');
  });

  it('toasts queue, keep at most three, and dismiss by id', () => {
    for (const t of currentToasts()) dismissToast(t.id);
    toast('one');
    toast('two');
    toast('three');
    toast('four');
    expect(currentToasts().map((t) => t.message)).toEqual(['two', 'three', 'four']);
    const first = currentToasts()[0];
    if (first !== undefined) dismissToast(first.id);
    expect(currentToasts().map((t) => t.message)).toEqual(['three', 'four']);
  });
});

describe('feedback', () => {
  it('Loading is a skeleton with a spoken label', () => {
    const markup = html(<Loading label="Loading visits" kind="chart" />);
    expect(markup).toContain('role="status"');
    expect(textOf(markup)).toBe('Loading visits');
  });

  it('EmptyState always has a title, and a reason when given', () => {
    const markup = html(<EmptyState title="No visits" reason="Try a longer period." />);
    expect(textOf(markup)).toBe('No visits Try a longer period.');
  });

  it('an error Alert is an alert; others are status', () => {
    expect(html(<Alert tone="error" title="Problem" />)).toContain('role="alert"');
    expect(html(<Alert tone="ok" title="Saved" />)).toContain('role="status"');
  });

  it('ErrorNotice shows the full trace id, never truncated (F15.AC2)', () => {
    const error = {
      code: 'INTERNAL',
      message: 'The server could not complete the request.',
      fields: [],
      retryAfter: null,
      traceId: '01J9X4TQ7B2M5ZK8R1C3V6N0PW',
    } as unknown as ApiError;
    expect(textOf(html(<ErrorNotice error={error} />))).toContain('01J9X4TQ7B2M5ZK8R1C3V6N0PW');
  });
});

describe('helpers', () => {
  it('middle-truncates long identifiers and leaves short ones alone', () => {
    expect(middleTruncate('01J9X4TQ7B2M5ZK8R1C3V6N0PW')).toBe('01J9X4…N0PW');
    expect(middleTruncate('AS55836')).toBe('AS55836');
  });

  it('says recent times relatively', () => {
    const now = Date.parse('2026-10-03T10:00:00Z');
    expect(relativeTime('2026-10-03T09:59:50Z', now)).toBe('just now');
    expect(relativeTime('2026-10-03T09:57:00Z', now)).toBe('3 minutes ago');
    expect(relativeTime('2026-10-03T07:00:00Z', now)).toBe('3 hours ago');
  });

  it('Identifier shows the short form and copies the whole value', () => {
    const markup = html(<Identifier value="01J9X4TQ7B2M5ZK8R1C3V6N0PW" />);
    expect(markup).toContain('title="01J9X4TQ7B2M5ZK8R1C3V6N0PW"');
    expect(textOf(markup)).toContain('01J9X4…N0PW');
  });
});
