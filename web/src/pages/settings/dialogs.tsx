/**
 * The two dialogs every Settings page shares (DESIGN §10.8):
 *
 * - **ConfirmDialog** (UI-16, §12 E24): names the object and the consequence before anything
 *   destructive happens; optionally makes you type a phrase (deleting an admin).
 * - **ShownOnceDialog** (§12 E25): new recovery codes or a setup link, in a locked dialog --
 *   no ×, no Escape, no backdrop -- whose Done stays disabled until "I have saved these" is
 *   ticked (owner decision, 2026-10-06). Its content lives only in the caller's state.
 */

import { useState, type ReactNode } from 'react';
import type { ApiError } from '@/api/client';
import { Button, Checkbox, Dialog, ErrorNotice, Field } from '@/components/ui';

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busyLabel,
  danger = true,
  typed,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  readonly open: boolean;
  readonly title: string;
  /** What will happen, in a sentence or two. */
  readonly children: ReactNode;
  readonly confirmLabel: string;
  readonly busyLabel?: string;
  readonly danger?: boolean;
  /** When set, the confirm button waits until this exact text is typed (UI-16). */
  readonly typed?: { readonly label: string; readonly phrase: string } | undefined;
  readonly busy: boolean;
  readonly error: ApiError | null;
  readonly onConfirm: () => void;
  readonly onClose: () => void;
}): React.JSX.Element {
  const [entered, setEntered] = useState('');
  const ready = typed === undefined || entered.trim() === typed.phrase;
  return (
    <Dialog
      open={open}
      size="sm"
      title={title}
      dismissible={!busy}
      onClose={() => {
        setEntered('');
        onClose();
      }}
      footer={
        <>
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              setEntered('');
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            disabled={!ready}
            busy={busy}
            busyLabel={busyLabel}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="t-secondary">{children}</div>
        {typed !== undefined && (
          <Field
            label={typed.label}
            value={entered}
            onChange={setEntered}
            autoComplete="off"
            autoFocus
          />
        )}
        {error !== null && <ErrorNotice error={error} />}
      </div>
    </Dialog>
  );
}

export function ShownOnceDialog({
  open,
  title,
  warning,
  savedLabel,
  children,
  onDone,
}: {
  readonly open: boolean;
  readonly title: string;
  /** Why it matters: "The previous set no longer works." */
  readonly warning: ReactNode;
  /** "I have saved these codes". */
  readonly savedLabel: string;
  readonly children: ReactNode;
  readonly onDone: () => void;
}): React.JSX.Element {
  const [saved, setSaved] = useState(false);
  return (
    <Dialog
      open={open}
      title={title}
      locked
      onClose={onDone}
      footer={
        <Button
          variant="primary"
          disabled={!saved}
          onClick={() => {
            setSaved(false);
            onDone();
          }}
        >
          Done
        </Button>
      }
    >
      <div className="stack">
        <p className="t-secondary m-0">{warning}</p>
        {children}
        <Checkbox label={savedLabel} checked={saved} onChange={setSaved} />
      </div>
    </Dialog>
  );
}
