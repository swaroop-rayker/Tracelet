/**
 * Settings › Preferences › Saved views (F9.AC26, DESIGN §12 E12, §16 M7.7): your own views --
 * open, rename, delete. Nobody else's appear here, the owner's included.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import {
  VIEWS,
  deleteView,
  renameView,
  savedViewListSchema,
  viewHref,
  type SavedView,
} from '@/api/workflow';
import { Panel } from '@/components/Panel';
import { ALL_ITEMS } from '@/components/shell/nav';
import { Button, DataTable, Dialog, ErrorNotice, Field, toast } from '@/components/ui';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

function pageName(path: string): string {
  if (path.startsWith('/links/')) return `Link ${path.slice('/links/'.length)}`;
  return ALL_ITEMS.find((i) => i.to === path)?.label ?? path;
}

export function SavedViewsCard(): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const query = useApi(VIEWS, null, savedViewListSchema);
  const [renaming, setRenaming] = useState<SavedView | null>(null);
  const [deleting, setDeleting] = useState<SavedView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function remove(view: SavedView): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await deleteView(me.csrf_token, view.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setDeleting(null);
    toast(`Deleted “${view.name}”.`);
    void client.invalidateQueries({ queryKey: [VIEWS] });
  }

  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="Saved views"
        description="Pages with their filters, saved from the filter bar. Only you see them."
        isEmpty={(d) => d.length === 0}
        empty="No saved views yet. Use the bookmark in a page's filter bar to save one."
      >
        {(views) => (
          <DataTable
            caption="Your saved views"
            rowKey={(v) => v.id}
            rows={views}
            columns={[
              {
                key: 'name',
                header: 'Name',
                render: (v) => (
                  <Link className="link" to={viewHref(v)}>
                    {v.name}
                  </Link>
                ),
              },
              { key: 'page', header: 'Page', render: (v) => pageName(v.path) },
              {
                key: 'actions',
                header: <span className="sr-only">Actions</span>,
                render: (v) => (
                  <span className="row">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Edit"
                      onClick={() => {
                        setRenaming(v);
                      }}
                    >
                      Rename
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Delete"
                      onClick={() => {
                        setError(null);
                        setDeleting(v);
                      }}
                    >
                      Delete
                    </Button>
                  </span>
                ),
              },
            ]}
          />
        )}
      </Panel>
      {renaming !== null && (
        <RenameDialog
          view={renaming}
          onClose={() => {
            setRenaming(null);
          }}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this saved view?"
        confirmLabel="Delete view"
        busyLabel="Deleting…"
        busy={busy}
        error={error}
        onConfirm={() => {
          if (deleting !== null) void remove(deleting);
        }}
        onClose={() => {
          setDeleting(null);
        }}
      >
        “{deleting?.name}” leaves your sidebar and the command palette. The page itself is
        untouched.
      </ConfirmDialog>
    </>
  );
}

function RenameDialog({
  view,
  onClose,
}: {
  readonly view: SavedView;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const [name, setName] = useState(view.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const trimmed = name.trim();

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await renameView(me.csrf_token, view.id, trimmed);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast('Renamed.');
    void client.invalidateQueries({ queryKey: [VIEWS] });
    onClose();
  }

  return (
    <Dialog
      open
      size="sm"
      title="Rename saved view"
      dismissible={!busy}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={busy}
            busyLabel="Saving…"
            disabled={trimmed === '' || trimmed === view.name}
            onClick={() => {
              void save();
            }}
          >
            Rename
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Name" value={name} onChange={setName} maxLength={60} autoFocus />
        {error !== null && <ErrorNotice error={error} />}
      </div>
    </Dialog>
  );
}
