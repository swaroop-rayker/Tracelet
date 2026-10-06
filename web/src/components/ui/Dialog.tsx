/**
 * Dialog and Drawer (DESIGN §5.5), on the native `<dialog>` element (ADR-0019).
 *
 * `showModal()` gives a focus trap, an inert background, Escape, and focus returned to the
 * opener when it closes. Background scrolling is locked by `body:has(dialog[open])` in the
 * stylesheet, not an injected style (the CSP forbids those). Content renders only while
 * open, so a closed dialog fetches nothing.
 *
 * A confirmation for a destructive action names the object and the consequence and uses a
 * `danger` primary labelled with the verb (DESIGN UI-16).
 */

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { IconButton } from '@/components/ui/Button';
import { cx } from '@/components/ui/util';

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  drawer = false,
  wide = false,
  side = 'right',
  dismissible = true,
  locked = false,
  className,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly title: ReactNode;
  readonly children: ReactNode;
  readonly footer?: ReactNode;
  readonly size?: 'sm' | 'md' | 'lg';
  /** Dock to the right edge as a full-height sheet. */
  readonly drawer?: boolean;
  /** A wider drawer (720 px), for detail views. */
  readonly wide?: boolean;
  /** Which edge a drawer docks to: right for detail, left for navigation. */
  readonly side?: 'left' | 'right';
  /** A click on the backdrop closes it. Off for forms that would lose input. */
  readonly dismissible?: boolean;
  /**
   * Shown-once content (DESIGN §12 E25): no ×, no Escape, no backdrop. The footer's own button
   * is the only way out, and the caller enables it once the content has been saved.
   */
  readonly locked?: boolean;
  readonly className?: string;
}): React.JSX.Element {
  const ref = useRef<HTMLDialogElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    if (open && !el.open) {
      el.showModal();
      // Start on the title, not the first control: a screen reader announces what the dialog
      // is, and the close button's tooltip does not pop up on open. A field marked
      // `data-autofocus` (a form's first input) takes precedence.
      const first =
        el.querySelector<HTMLElement>('[data-autofocus]') ??
        el.querySelector<HTMLElement>('.dialog__title');
      first?.focus();
    }
    if (!open && el.open) el.close();
  }, [open]);

  useEffect(() => {
    const el = ref.current;
    if (el === null) return undefined;
    const handle = (): void => {
      // Chromium lets a second Escape close even a dialog whose `cancel` was refused. A
      // locked dialog the caller still wants open re-opens, rather than vanishing with the
      // only copy of its codes.
      if (locked && open) {
        el.showModal();
        return;
      }
      onClose();
    };
    // Escape fires `cancel` first; a locked dialog refuses it.
    const cancel = (event: Event): void => {
      if (locked) event.preventDefault();
    };
    el.addEventListener('close', handle);
    el.addEventListener('cancel', cancel);
    return () => {
      el.removeEventListener('close', handle);
      el.removeEventListener('cancel', cancel);
    };
  }, [onClose, locked, open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className={cx(
        'dialog',
        size !== 'md' && !drawer && `dialog--${size}`,
        drawer && 'drawer',
        drawer && wide && 'drawer--wide',
        drawer && side === 'left' && 'drawer--left',
        className,
      )}
      onClick={(event) => {
        // A click on the dialog element itself, not its content, is a click on the backdrop.
        if (dismissible && !locked && event.target === event.currentTarget) onClose();
      }}
    >
      {open && (
        <>
          <header className="dialog__head">
            <h2 id={titleId} className="t-section dialog__title" tabIndex={-1}>
              {title}
            </h2>
            {!locked && <IconButton icon="Close" label="Close" onClick={onClose} />}
          </header>
          <div className="dialog__body">{children}</div>
          {footer !== undefined && <footer className="dialog__foot">{footer}</footer>}
        </>
      )}
    </dialog>
  );
}
