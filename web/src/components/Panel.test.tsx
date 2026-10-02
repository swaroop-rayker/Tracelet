/**
 * "No panel can render blank" (F9.AC18, the B5 defence), exercised for every state.
 *
 * Rendered to static markup in Node: what is under test is that each state produces
 * readable text, which needs no DOM. Every dashboard chart and table goes through
 * `PanelView`, and `empty` is a required prop, so these four states are all the states
 * any panel has.
 */

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ApiFailure } from '@/api/query';
import type { Meta } from '@/api/schemas';
import { PanelView, type PanelState } from '@/components/Panel';

const META: Meta = {
  start: '2026-09-03T00:00:00+05:30',
  end: '2026-10-03T00:00:00+05:30',
  reporting_tz: 'Asia/Kolkata',
  computed_from: 'rollup',
  refreshed_at: '2026-10-02T06:35:00Z',
  stage_mix: { total: 200, server: 2, enriched: 150, server_only: 40, rate_limited: 8 },
};

interface Data {
  readonly rows: readonly string[];
  readonly meta: Meta;
}

function render(state: PanelState<Data>): string {
  return renderToStaticMarkup(
    <PanelView<Data>
      state={state}
      title="Visits by country"
      isEmpty={(d) => d.rows.length === 0}
      empty="No visits in this period with these filters."
      meta={(d) => d.meta}
    >
      {(d) => (
        <ul>
          {d.rows.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </PanelView>,
  );
}

function textOf(html: string): string {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

describe('every panel state renders something a person can read', () => {
  it('loading says what is loading', () => {
    const html = render({ status: 'pending' });
    expect(textOf(html)).toContain('Loading visits by country');
    expect(html).toContain('aria-busy="true"');
  });

  it('error shows the API message and the trace id', () => {
    const error = new ApiFailure({
      code: 'INTERNAL_ERROR',
      message: 'The server could not complete the request.',
      status: 500,
      traceId: '01J9TRACE',
      fields: [],
      retryAfter: null,
    });
    const text = textOf(render({ status: 'error', error }));
    expect(text).toContain('The server could not complete the request.');
    expect(text).toContain('01J9TRACE');
  });

  it('empty says why it is empty, and still states what it was computed over', () => {
    const text = textOf(render({ status: 'success', data: { rows: [], meta: META } }));
    expect(text).toContain('No visits in this period with these filters.');
    expect(text).toContain('200 requests');
  });

  it('data renders the rows and the stage mix (F9.AC20)', () => {
    const text = textOf(
      render({ status: 'success', data: { rows: ['India', 'Nepal'], meta: META } }),
    );
    expect(text).toContain('India');
    expect(text).toContain('75% enriched');
    expect(text).toContain('20% server-only');
    expect(text).toContain('8 rate-limited');
    expect(text).toContain('from rollups');
  });

  it('never renders an empty body in any state', () => {
    const states: PanelState<Data>[] = [
      { status: 'pending' },
      {
        status: 'error',
        error: new ApiFailure({
          code: 'TRANSPORT',
          message: 'Could not reach the server.',
          status: null,
          traceId: null,
          fields: [],
          retryAfter: null,
        }),
      },
      { status: 'success', data: { rows: [], meta: META } },
      { status: 'success', data: { rows: ['x'], meta: META } },
    ];
    for (const state of states) {
      const body = /<div class="panel-body">([\s\S]*?)<\/div>/.exec(render(state))?.[1] ?? '';
      expect(textOf(body).length, state.status).toBeGreaterThan(0);
    }
  });
});
