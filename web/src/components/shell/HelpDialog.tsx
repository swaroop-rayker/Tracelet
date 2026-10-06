/**
 * Help (DESIGN §9.2): keyboard shortcuts and the glossary of the dashboard's own terms
 * (§7.4). No outbound links: the dashboard makes no third-party request (ADR-0017).
 */

import { Dialog, Kbd, modKey } from '@/components/ui';
import { ALL_ITEMS } from '@/components/shell/nav';

const GLOSSARY: readonly (readonly [string, string])[] = [
  [
    'Best guess',
    'Where the engine thinks a visit most likely was: the highest-confidence value at each level, shown with its confidence.',
  ],
  [
    'Confirmed',
    'A level that passed the strict threshold. Only confirmed locations drive geofencing and alerts.',
  ],
  [
    'Enriched',
    'The visitor’s browser added client-side details. Server-only visits were captured without them, which is normal in in-app browsers.',
  ],
  ['Rate-limited', 'Requests shed under load. They are counted, never inferred.'],
  [
    'Stage mix',
    'The share of requests that were enriched, server-only, pending or rate-limited. Every figure states it, so a chart cannot mislead when coverage shifts.',
  ],
  [
    'Rollups or raw rows',
    'Most figures come from pre-computed daily rollups; filters the rollups cannot serve are answered from raw visits with the same definitions.',
  ],
  [
    'Automated traffic',
    'Bots, crawlers, datacenter clients, spam and spoofed visits. Excluded by default; include it from the filter menu.',
  ],
  [
    'Visitor',
    'A pseudonymous identity from the browser fingerprint; the same person on a new device is a new visitor.',
  ],
];

export function HelpDialog({
  open,
  onClose,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
}): React.JSX.Element {
  const mod = modKey();
  return (
    <Dialog open={open} onClose={onClose} title="Help" size="lg">
      <section aria-labelledby="help-keys">
        <h3 id="help-keys" className="t-section help__heading">
          Keyboard shortcuts
        </h3>
        <table className="dt dt--compact help__keys">
          <caption className="sr-only">Keyboard shortcuts</caption>
          <tbody>
            <tr>
              <td>
                <Kbd>{mod}</Kbd> <Kbd>K</Kbd> or <Kbd>/</Kbd>
              </td>
              <td>Search pages and actions, or paste a visit id</td>
            </tr>
            {ALL_ITEMS.map((item) => (
              <tr key={item.key}>
                <td>
                  <Kbd>g</Kbd> <Kbd>{item.key}</Kbd>
                </td>
                <td>Go to {item.label}</td>
              </tr>
            ))}
            <tr>
              <td>
                <Kbd>[</Kbd>
              </td>
              <td>Collapse or expand the sidebar</td>
            </tr>
            <tr>
              <td>
                <Kbd>?</Kbd>
              </td>
              <td>This help</td>
            </tr>
            <tr>
              <td>
                <Kbd>Esc</Kbd>
              </td>
              <td>Close a menu, a dialog or the search</td>
            </tr>
          </tbody>
        </table>
      </section>
      <section aria-labelledby="help-terms">
        <h3 id="help-terms" className="t-section help__heading">
          Terms
        </h3>
        <dl className="kv">
          {GLOSSARY.map(([term, meaning]) => (
            <div key={term} className="contents">
              <dt>{term}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
      </section>
    </Dialog>
  );
}
