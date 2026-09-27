/**
 * M0 skeleton.
 *
 * Deliberately small, but it proves three things end to end: the SPA builds and
 * is served by Caddy, Caddy proxies the API surfaces, and the trace id survives
 * the round trip.
 *
 * It also establishes the pattern F9.AC18 makes mandatory for every panel from
 * M5 onward — explicit loading, error and empty states. A blank panel is a
 * defect (B5), so there is no code path here that renders nothing.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchReadiness, type ApiResult, type Readiness } from '@/api/health';

type State =
  | { readonly phase: 'loading' }
  | { readonly phase: 'ready'; readonly result: ApiResult<Readiness> };

export default function App(): React.JSX.Element {
  const [state, setState] = useState<State>({ phase: 'loading' });

  const load = useCallback(async (): Promise<void> => {
    setState({ phase: 'loading' });
    const result = await fetchReadiness();
    setState({ phase: 'ready', result });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="shell">
      <header>
        <h1>Tracelet</h1>
        <p className="muted">M0 — foundation. No product feature yet, by design.</p>
      </header>

      <section aria-labelledby="status-heading">
        <h2 id="status-heading">System readiness</h2>
        {state.phase === 'loading' ? <Loading /> : <Result result={state.result} />}
        <button type="button" onClick={() => void load()}>
          Refresh
        </button>
      </section>
    </main>
  );
}

function Loading(): React.JSX.Element {
  return (
    <p className="muted" role="status">
      Checking…
    </p>
  );
}

function Result({ result }: { readonly result: ApiResult<Readiness> }): React.JSX.Element {
  if (result.kind === 'failure') {
    return (
      <div className="card error" role="alert">
        <strong>Unavailable</strong>
        <p>{result.message}</p>
        {result.traceId !== null && (
          <p className="muted mono">
            trace {result.traceId}
            {/* F15.AC2: the id is surfaced so a report is diagnosable from one
                identifier, without needing to reproduce anything. */}
          </p>
        )}
      </div>
    );
  }

  const { ready, environment, checks } = result.data;
  const entries = Object.entries(checks);

  return (
    <div className={ready ? 'card ok' : 'card warn'}>
      <strong>{ready ? 'Ready' : 'Not ready'}</strong>
      <p className="muted">environment: {environment}</p>
      {entries.length === 0 ? (
        <p className="muted">No checks reported.</p>
      ) : (
        <ul className="checks">
          {entries.map(([name, check]) => (
            <li key={name}>
              <span aria-hidden="true">{check.ok ? '✓' : '✗'}</span>
              {/* Never colour alone — NFR7.AC3 applies to status as much as charts. */}
              <span className="sr-only">{check.ok ? 'pass' : 'fail'}</span>
              <code>{name}</code>
              <span className="muted">{check.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
