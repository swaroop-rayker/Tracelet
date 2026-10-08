/**
 * Save view (F9.AC26, DESIGN §12 E12, §16 M7.7): the current page and its filters, under a
 * name, in the admin's own list -- the sidebar's Saved views group and the command palette.
 * A view is exactly the URL the page already keeps (F9.AC13), so it reopens as it was.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useLocation } from 'react-router';
import type { ApiError } from '@/api/client';
import { VIEWS, createView } from '@/api/workflow';
import { Button, Dialog, ErrorNotice, Field, IconButton, toast } from '@/components/ui';
import { useSession } from '@/session';

const NAME_MAX = 60;

export function SaveViewButton(): React.JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <>
      <IconButton
        icon="SaveView"
        label="Save this view"
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      />
      {open && (
        <SaveViewDialog
          onClose={() => {
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function SaveViewDialog({ onClose }: { readonly onClose: () => void }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const location = useLocation();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const trimmed = name.trim();
  const nameError = trimmed.length > NAME_MAX ? `At most ${String(NAME_MAX)} characters.` : null;

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await createView(me.csrf_token, {
      name: trimmed,
      path: location.pathname,
      query: location.search.replace(/^\?/, ''),
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(`Saved “${trimmed}”.`);
    void client.invalidateQueries({ queryKey: [VIEWS] });
    onClose();
  }

  return (
    <Dialog
      open
      size="sm"
      title="Save this view"
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
            disabled={trimmed === '' || nameError !== null}
            onClick={() => {
              void save();
            }}
          >
            Save view
          </Button>
        </>
      }
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed !== '' && nameError === null) void save();
        }}
      >
        <p className="t-secondary m-0">
          This page and its period and filters, in your own list. Only you see it.
        </p>
        <Field
          label="Name"
          value={name}
          onChange={setName}
          maxLength={NAME_MAX}
          placeholder="Karnataka, mobile, last 7 days"
          error={nameError}
          autoFocus
        />
        {error !== null && <ErrorNotice error={error} />}
      </form>
    </Dialog>
  );
}
