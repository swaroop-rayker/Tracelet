/**
 * Settings › System (DESIGN §10.8): whether this server is ready, check by check. A 503 from
 * `/readyz` is an answer, not an error -- an instance behind its migrations says so -- so it
 * renders as data. M7's Health page takes this over.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchReadiness, type ApiResult, type Readiness } from '@/api/health';
import { Alert, Badge, Button, Card, Identifier, Loading, SettingRow } from '@/components/ui';

export default function SystemPage(): React.JSX.Element {
  const [result, setResult] = useState<ApiResult<Readiness> | null>(null);
  const [busy, setBusy] = useState(false);

  const check = useCallback(async (): Promise<void> => {
    setBusy(true);
    setResult(await fetchReadiness());
    setBusy(false);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  return (
    <Card
      title="Readiness"
      description="What the server checks before it reports itself ready."
      actions={
        <Button
          variant="secondary"
          busy={busy}
          busyLabel="Checking…"
          onClick={() => {
            void check();
          }}
        >
          Re-check
        </Button>
      }
    >
      {result === null && <Loading label="Checking readiness…" />}
      {result !== null && result.kind === 'failure' && (
        <Alert tone="error" title="Readiness could not be checked">
          {result.message}
          {result.traceId !== null && (
            <span className="block">
              Trace <Identifier value={result.traceId} label="Copy trace id" />
            </span>
          )}
        </Alert>
      )}
      {result !== null && result.kind === 'success' && (
        <>
          <SettingRow
            title={result.data.ready ? 'Ready' : 'Not ready'}
            description={`Environment: ${result.data.environment}`}
          >
            <Badge tone={result.data.ready ? 'ok' : 'error'}>
              {result.data.ready ? 'Ready' : 'Not ready'}
            </Badge>
          </SettingRow>
          {Object.entries(result.data.checks).map(([name, c]) => (
            <SettingRow
              key={name}
              title={<span className="t-mono">{name}</span>}
              description={c.detail}
            >
              <Badge tone={c.ok ? 'ok' : 'error'}>{c.ok ? 'Pass' : 'Fail'}</Badge>
            </SettingRow>
          ))}
        </>
      )}
    </Card>
  );
}
