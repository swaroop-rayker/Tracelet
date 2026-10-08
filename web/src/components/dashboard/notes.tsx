/**
 * Annotations (F9.AC25, DESIGN §12 E34, §16 M7.7): notes pinned to a moment, drawn on the time
 * charts and listed in a panel.
 *
 * Any admin adds a note and changes or deletes their own; an owner deletes anyone's (audited;
 * SPEC §11 row 29). Someone else's note shows its actions disabled with the reason (UI-17), and
 * deleting is a confirm naming the note (UI-16). Times are typed in the reporting time zone,
 * the zone the charts' buckets are cut in, so a note lands in the bucket it says.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { ApiError } from '@/api/client';
import { useApi } from '@/api/query';
import { isoToZoned, notesParams, zonedToIso } from '@/notes';
import { linkChoicesSchema } from '@/api/schemas';
import {
  NOTES,
  annotationListSchema,
  createNote,
  deleteNote,
  updateNote,
  type Annotation,
} from '@/api/workflow';
import { Panel } from '@/components/Panel';
import {
  Button,
  DataTable,
  Dialog,
  ErrorNotice,
  Field,
  Select,
  Timestamp,
  toast,
} from '@/components/ui';
import { ConfirmDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

const MAX = 200;

export function NoteDialog({
  open,
  note,
  defaultLink,
  onClose,
}: {
  readonly open: boolean;
  /** Editing this note; null adds a new one. */
  readonly note: Annotation | null;
  readonly defaultLink: string | null;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me } = useSession();
  const zone = me.reporting_tz;
  const client = useQueryClient();
  const links = useApi('/api/v1/links', null, linkChoicesSchema);
  const [when, setWhen] = useState(() => isoToZoned(note?.at ?? new Date().toISOString(), zone));
  const [text, setText] = useState(note?.text ?? '');
  const [link, setLink] = useState(note === null ? (defaultLink ?? '') : (note.link_id ?? ''));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const at = zonedToIso(when, zone);
  const whenError = at === null ? 'A time is YYYY-MM-DD HH:MM.' : null;
  const textError =
    text.trim().length === 0
      ? 'Write the note.'
      : text.trim().length > MAX
        ? `At most ${String(MAX)} characters.`
        : null;

  async function save(): Promise<void> {
    if (at === null || textError !== null) return;
    setBusy(true);
    setError(null);
    const fields = { at, text: text.trim(), link_id: link === '' ? null : link };
    const result =
      note === null
        ? await createNote(me.csrf_token, fields)
        : await updateNote(me.csrf_token, note.id, fields);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    toast(note === null ? 'Note added.' : 'Note saved.');
    void client.invalidateQueries({ queryKey: [NOTES] });
    onClose();
  }

  return (
    <Dialog
      open={open}
      size="sm"
      title={note === null ? 'Add a note' : 'Edit note'}
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
            disabled={whenError !== null || textError !== null}
            onClick={() => {
              void save();
            }}
          >
            {note === null ? 'Add note' : 'Save note'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="When"
          value={when}
          onChange={setWhen}
          mono
          maxLength={16}
          hint={`In ${zone}, as the charts are.`}
          error={whenError}
        />
        <Field
          label="Note"
          value={text}
          onChange={setText}
          maxLength={MAX}
          placeholder="Posted the reel"
          error={text === '' ? null : textError}
          autoFocus
        />
        <Select
          label="Link"
          hideLabel={false}
          value={link}
          onChange={setLink}
          options={[
            { value: '', label: 'All links' },
            ...(links.data ?? [])
              .filter((l) => l.archived_at === null || l.id === link)
              .map((l) => ({ value: l.id, label: `${l.label} (${l.slug})` })),
          ]}
        />
        {error !== null && <ErrorNotice error={error} />}
      </div>
    </Dialog>
  );
}

/** "Add note", for a chart's actions. */
export function AddNoteButton({ linkId }: { readonly linkId: string | null }): React.JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        icon="Note"
        onClick={() => {
          setOpen(true);
        }}
      >
        Add note
      </Button>
      {open && (
        <NoteDialog
          open
          note={null}
          defaultLink={linkId}
          onClose={() => {
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

/** Every note in the window, with who wrote it, to edit or delete. */
export function NotesPanel({ params }: { readonly params: URLSearchParams }): React.JSX.Element {
  const { me } = useSession();
  const client = useQueryClient();
  const query = useApi(NOTES, notesParams(params), annotationListSchema);
  const [editing, setEditing] = useState<Annotation | null>(null);
  const [deleting, setDeleting] = useState<Annotation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const owner = me.role === 'owner';

  async function remove(note: Annotation): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await deleteNote(me.csrf_token, note.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setDeleting(null);
    toast('Note deleted.');
    void client.invalidateQueries({ queryKey: [NOTES] });
  }

  return (
    <>
      <Panel
        query={query}
        kind="table"
        title="Notes"
        description="What happened when: drawn on the time charts as ✎. Anyone can add one; only its author changes it."
        actions={<AddNoteButton linkId={params.get('link_id')} />}
        isEmpty={(d) => d.items.length === 0}
        empty="No notes in this period. Add one to mark a post, a campaign or a change."
      >
        {(d) => (
          <DataTable
            caption="Notes"
            rowKey={(n) => n.id}
            rows={[...d.items].reverse()}
            columns={[
              {
                key: 'when',
                header: 'When',
                render: (n) => <Timestamp iso={n.at} zone={me.timezone} mode="absolute" />,
              },
              { key: 'note', header: 'Note', render: (n) => n.text },
              {
                key: 'link',
                header: 'Link',
                render: (n) => n.link_label ?? <span className="muted">All links</span>,
              },
              {
                key: 'by',
                header: 'By',
                render: (n) => n.author?.name ?? <span className="muted">A former admin</span>,
              },
              {
                key: 'actions',
                header: <span className="sr-only">Actions</span>,
                render: (n) => (
                  <span className="row">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Edit"
                      disabledReason={n.mine ? null : 'Only its author can change this note.'}
                      onClick={() => {
                        setEditing(n);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon="Delete"
                      disabledReason={
                        n.mine || owner ? null : 'Only its author or an owner can delete this.'
                      }
                      onClick={() => {
                        setError(null);
                        setDeleting(n);
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
      {editing !== null && (
        <NoteDialog
          open
          note={editing}
          defaultLink={null}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this note?"
        confirmLabel="Delete note"
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
        “{deleting?.text}” leaves every chart. The deletion is recorded in the audit log.
      </ConfirmDialog>
    </>
  );
}
