/**
 * Recovery codes, shown once (F8): a selectable grid, "Copy all", and "Download as .txt"
 * (DESIGN §12 E10) -- a file generated in the browser, so the codes never travel again.
 */

import { Button, CopyButton } from '@/components/ui';

function asText(codes: readonly string[]): string {
  const issued = new Date().toISOString().slice(0, 10);
  return [
    'Tracelet recovery codes',
    `Issued ${issued}. Each code signs you in once, without your password or authenticator.`,
    'Keep them somewhere safe; anyone holding one can sign in as you.',
    '',
    ...codes,
    '',
  ].join('\n');
}

export function RecoveryCodes({ codes }: { readonly codes: readonly string[] }): React.JSX.Element {
  return (
    <div className="recovery">
      <ul className="codes t-mono" aria-label="Recovery codes">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="recovery__actions">
        <Button
          size="sm"
          icon="Download"
          onClick={() => {
            const blob = new Blob([asText(codes)], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = 'tracelet-recovery-codes.txt';
            link.click();
            window.setTimeout(() => {
              URL.revokeObjectURL(url);
            }, 0);
          }}
        >
          Download as .txt
        </Button>
        <span className="recovery__copy">
          <CopyButton value={codes.join('\n')} label="Copy all codes" />
          <span className="t-meta">Copy all</span>
        </span>
      </div>
    </div>
  );
}
