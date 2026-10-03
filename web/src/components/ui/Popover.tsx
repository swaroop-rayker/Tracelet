/**
 * Popover and Menu (DESIGN §5.5), on the native `popover` attribute (ADR-0019).
 *
 * The browser gives the hard parts: the top layer, light dismiss (a click outside), and
 * Escape. The trigger is wired with `popovertarget`, so clicking it while the popover is open
 * closes it rather than re-opening it. This module adds anchoring (`position.ts`), returning
 * focus to the trigger, and -- for menus -- arrow-key movement and typeahead.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Icon, type IconName } from '@/components/icons';
import { follow, type Align } from '@/components/ui/position';
import { cx } from '@/components/ui/util';

export interface TriggerProps {
  readonly popoverTarget: string;
  readonly 'aria-expanded': boolean;
  readonly 'aria-controls': string;
  readonly 'aria-haspopup'?: 'menu' | 'dialog';
  readonly ref: React.RefObject<HTMLButtonElement | null>;
}

export function Popover({
  trigger,
  children,
  align = 'start',
  role = 'dialog',
  label,
  className,
  onOpenChange,
}: {
  /** Render the trigger button, spreading these props onto it (it must be a `<button>`). */
  readonly trigger: (props: TriggerProps) => ReactNode;
  /** The content; `close` hides the popover and returns focus to the trigger. */
  readonly children: (close: () => void) => ReactNode;
  readonly align?: Align;
  readonly role?: 'dialog' | 'menu';
  /** The accessible name of the popover. */
  readonly label: string;
  readonly className?: string;
  readonly onOpenChange?: (open: boolean) => void;
}): React.JSX.Element {
  const id = useId();
  const anchor = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const el = pop.current;
    if (el === null) return undefined;
    let stop: (() => void) | null = null;
    const onToggle = (event: Event): void => {
      const isOpen = (event as ToggleEvent).newState === 'open';
      setOpen(isOpen);
      onOpenChange?.(isOpen);
      stop?.();
      stop = null;
      if (isOpen && anchor.current !== null) {
        stop = follow(anchor.current, el, { side: 'below', align });
        if (role === 'menu') focusItem(el, 'first');
      } else if (el.contains(document.activeElement) || document.activeElement === document.body) {
        anchor.current?.focus();
      }
    };
    el.addEventListener('toggle', onToggle);
    return () => {
      el.removeEventListener('toggle', onToggle);
      stop?.();
    };
  }, [align, role, onOpenChange]);

  const close = (): void => {
    if (pop.current?.matches(':popover-open') === true) pop.current.hidePopover();
    anchor.current?.focus();
  };

  return (
    <>
      {trigger({
        popoverTarget: id,
        'aria-expanded': open,
        'aria-controls': id,
        'aria-haspopup': role,
        ref: anchor,
      })}
      <div
        ref={pop}
        id={id}
        popover="auto"
        role={role}
        aria-label={label}
        className={cx('pop', className)}
        onKeyDown={role === 'menu' ? menuKeys : undefined}
      >
        {open && children(close)}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

const ITEM = '[role^="menuitem"]:not([disabled])';

function items(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(ITEM)];
}

function focusItem(root: HTMLElement, which: 'first' | 'last'): void {
  // After the content renders (the popover renders children only while open).
  window.requestAnimationFrame(() => {
    const list = items(root);
    const checked = list.find((el) => el.getAttribute('aria-checked') === 'true');
    (which === 'first' ? (checked ?? list[0]) : list.at(-1))?.focus();
  });
}

/** Arrow keys, Home/End, and typeahead by first letter (WAI-ARIA menu pattern). */
function menuKeys(event: React.KeyboardEvent<HTMLDivElement>): void {
  const list = items(event.currentTarget);
  if (list.length === 0) return;
  const at = list.indexOf(document.activeElement as HTMLElement);
  let next: number | null = null;
  if (event.key === 'ArrowDown') next = (at + 1) % list.length;
  else if (event.key === 'ArrowUp') next = (at - 1 + list.length) % list.length;
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = list.length - 1;
  else if (event.key.length === 1 && /\S/.test(event.key)) {
    const letter = event.key.toLowerCase();
    const order = [...list.slice(at + 1), ...list.slice(0, at + 1)];
    const hit = order.find((el) => (el.textContent ?? '').trim().toLowerCase().startsWith(letter));
    if (hit !== undefined) next = list.indexOf(hit);
  }
  if (next !== null) {
    event.preventDefault();
    list[next]?.focus();
  }
}

/**
 * A menu button: the trigger shows `label` (and an optional icon) with a chevron.
 * Items are `MenuItem`s, separated by `MenuSeparator`s.
 */
export function Menu({
  label,
  triggerLabel,
  icon,
  align = 'start',
  variant = 'secondary',
  size = 'md',
  iconOnly = false,
  children,
}: {
  /** The menu's accessible name. */
  readonly label: string;
  /** What the trigger shows; defaults to `label`. */
  readonly triggerLabel?: ReactNode;
  readonly icon?: IconName;
  readonly align?: Align;
  readonly variant?: 'secondary' | 'ghost';
  readonly size?: 'md' | 'sm';
  /** An icon-only trigger (`•••`); the label becomes its accessible name. */
  readonly iconOnly?: boolean;
  readonly children: (close: () => void) => ReactNode;
}): React.JSX.Element {
  const Glyph = icon === undefined ? null : Icon[icon];
  return (
    <Popover
      role="menu"
      label={label}
      align={align}
      trigger={({ ref, ...props }) =>
        iconOnly ? (
          <button
            type="button"
            ref={ref}
            className={cx('icon-btn', size === 'sm' && 'icon-btn--sm')}
            aria-label={label}
            {...props}
          >
            {Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />}
          </button>
        ) : (
          <button
            type="button"
            ref={ref}
            className={cx('btn', variant === 'ghost' && 'btn--ghost', size === 'sm' && 'btn--sm')}
            {...props}
          >
            {Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />}
            {triggerLabel ?? label}
            <Icon.Chevron size={14} strokeWidth={1.75} aria-hidden="true" />
          </button>
        )
      }
    >
      {(close) => <div className="menu">{children(close)}</div>}
    </Popover>
  );
}

export function MenuItem({
  children,
  onSelect,
  icon,
  hint,
  checked,
  danger = false,
  disabled = false,
  href,
  download,
}: {
  readonly children: ReactNode;
  readonly onSelect?: () => void;
  readonly icon?: IconName;
  /** Muted text at the end: a shortcut or a count. */
  readonly hint?: ReactNode;
  /** A choice in a single-select menu (`menuitemradio`). */
  readonly checked?: boolean;
  readonly danger?: boolean;
  readonly disabled?: boolean;
  /** A link item (an export, a page). */
  readonly href?: string;
  readonly download?: boolean;
}): React.JSX.Element {
  const Glyph = icon === undefined ? null : Icon[icon];
  const content = (
    <>
      {Glyph !== null && <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />}
      <span>{children}</span>
      {hint !== undefined && <span className="menu__hint">{hint}</span>}
    </>
  );
  const className = cx('menu__item', danger && 'menu__item--danger');
  if (href !== undefined) {
    return (
      <a role="menuitem" className={className} href={href} download={download} onClick={onSelect}>
        {content}
      </a>
    );
  }
  return (
    <button
      type="button"
      role={checked === undefined ? 'menuitem' : 'menuitemradio'}
      aria-checked={checked}
      className={className}
      disabled={disabled}
      onClick={onSelect}
    >
      {content}
    </button>
  );
}

export function MenuSeparator(): React.JSX.Element {
  return <div className="menu__sep" role="separator" />;
}

export function MenuLabel({ children }: { readonly children: ReactNode }): React.JSX.Element {
  return (
    <div className="menu__label" role="presentation">
      {children}
    </div>
  );
}
