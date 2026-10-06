/**
 * Links (DESIGN §10.11, §12 E20): every tracking link, where it sends visitors, and its
 * all-time visit count, each opening its own dashboard. Creating and editing links is M7's
 * (F10.AC6); the one setting changed here is "Asks for location" (F1.AC11, ADR-0021).
 *
 * The request always names `include_archived`, so its cache entry is never the link
 * selector's, which parses the same endpoint with a narrower schema.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import { setAskLocation } from '@/api/links';
import { useApi } from '@/api/query';
import { linksSchema, type LinkSummary } from '@/api/schemas';
import { Panel } from '@/components/Panel';
import { PageHeader } from '@/components/shell/PageHeader';
import { Badge, DataTable, Switch, Timestamp, toast } from '@/components/ui';
import { count } from '@/format';
import { useSession } from '@/session';

/** "instagram.com/p/xyz…": enough to recognise, never the whole URL in a cell. */
function destination(url: string): string {
  try {
    const parsed = new URL(url);
    const rest = `${parsed.pathname}${parsed.search}`;
    const shown = `${parsed.host}${rest === '/' ? '' : rest}`;
    return shown.length > 40 ? `${shown.slice(0, 39)}…` : shown;
  } catch {
    return url;
  }
}

export function LinkStatus({ link }: { readonly link: LinkSummary }): React.JSX.Element {
  return (
    <span className="badges">
      {link.archived_at !== null ? (
        <Badge>Archived</Badge>
      ) : link.is_active ? (
        <Badge tone="ok">Active</Badge>
      ) : (
        <Badge tone="warn">Inactive</Badge>
      )}
      {link.is_default && <Badge tone="info">Default</Badge>}
    </span>
  );
}

/**
 * Whether the link asks visitors for their location (F1.AC11, ADR-0021): a consent screen and
 * the browser prompt, waiting up to 15 s. Owner only, optimistic and reversible (UI-13); an
 * analyst sees the setting as text (UI-17).
 */
function AskCell({ link }: { readonly link: LinkSummary }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [value, setValue] = useState(link.ask_location);
  if (me.role !== 'owner' || link.archived_at !== null) {
    return <span>{link.ask_location ? 'Yes' : 'No'}</span>;
  }
  return (
    <Switch
      label={`${link.label} asks for location`}
      hideLabel
      checked={value}
      onChange={(next) => {
        setValue(next);
        void setAskLocation(me.csrf_token, link.id, next).then((result) => {
          if (!result.ok) {
            setValue(!next);
            toast(`Could not change ${link.label}: ${result.error.message}`);
            return;
          }
          setValue(result.data.ask_location);
          toast(
            result.data.ask_location
              ? `${link.label} now asks visitors for their location.`
              : `${link.label} no longer asks for location.`,
          );
          void client.invalidateQueries({ queryKey: ['/api/v1/links'] });
        });
      }}
    />
  );
}

export default function LinksPage(): React.JSX.Element {
  const [archived, setArchived] = useState(false);
  const { me } = useSession();
  const location = useLocation();
  const query = useApi(
    '/api/v1/links',
    new URLSearchParams({ include_archived: String(archived) }),
    linksSchema,
  );
  return (
    <div className="page">
      <PageHeader
        title="Links"
        description="Every tracking link and where it sends visitors. Counts are all-time."
        actions={<Switch label="Show archived" checked={archived} onChange={setArchived} />}
      />
      <Panel
        query={query}
        kind="table"
        title="Tracking links"
        description="Open a link for its own dashboard, with the period and filters you are using."
        isEmpty={(d) => d.length === 0}
        empty={
          archived
            ? 'No links yet. Links are created from the API until M7 adds a form.'
            : 'No active links. Turn on “Show archived” to see archived ones.'
        }
      >
        {(links) => (
          <DataTable
            caption="Tracking links"
            rowKey={(l) => l.id}
            rows={links}
            columns={[
              {
                key: 'link',
                header: 'Link',
                render: (l) => (
                  <span className="link-cell">
                    <Link
                      className="link"
                      to={{ pathname: `/links/${l.slug}`, search: location.search }}
                    >
                      {l.label}
                    </Link>
                    <span className="t-meta mono">{l.slug}</span>
                  </span>
                ),
              },
              {
                key: 'destination',
                header: 'Destination',
                render: (l) => (
                  <span className="muted" title={l.destination_url}>
                    {destination(l.destination_url)}
                  </span>
                ),
              },
              { key: 'status', header: 'Status', render: (l) => <LinkStatus link={l} /> },
              {
                key: 'ask',
                header: 'Asks for location',
                render: (l) => <AskCell link={l} />,
              },
              {
                key: 'visits',
                header: 'Visits',
                numeric: true,
                render: (l) => count(l.visit_count),
              },
              {
                key: 'created',
                header: 'Created',
                render: (l) => <Timestamp iso={l.created_at} zone={me.timezone} />,
              },
            ]}
          />
        )}
      </Panel>
    </div>
  );
}
