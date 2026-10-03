/**
 * The command palette, Ctrl K / ⌘K (DESIGN §5.5, §12 E1): go to any page, find visits, jump to a
 * visit or visitor by id, switch theme, and the toolbar's actions -- all through existing routes
 * and URL keys, nothing new on the server.
 *
 * A native `<dialog>` holding a WAI-ARIA combobox: focus stays in the input, the arrow keys move
 * the active option (`aria-activedescendant`), Enter runs it, Escape closes.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { logout } from '@/api/auth';
import { Icon, type IconName } from '@/components/icons';
import { clearAll, setScalar } from '@/components/shell/filterDefs';
import { ALL_ITEMS } from '@/components/shell/nav';
import { useThemeSwitch } from '@/components/shell/useThemeSwitch';
import { Kbd } from '@/components/ui';
import { copyText } from '@/components/ui/util';
import { parseFilters, serializeFilters } from '@/filters';
import { useSession } from '@/session';
import { THEMES } from '@/theme';

interface Command {
  readonly id: string;
  readonly group: 'Go to' | 'Find' | 'Actions';
  readonly label: string;
  readonly hint?: string;
  readonly icon: IconName;
  readonly run: () => void;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VISITOR = /^[0-9a-f]{32}$/i;

export function CommandPalette({
  open,
  onClose,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
}): React.JSX.Element {
  const ref = useRef<HTMLDialogElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const commands = useCommands(query, onClose);

  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    if (open && !el.open) {
      setQuery('');
      setActive(0);
      el.showModal();
      input.current?.focus();
    }
    if (!open && el.open) el.close();
  }, [open]);

  useEffect(() => {
    const el = ref.current;
    if (el === null) return undefined;
    const handle = (): void => {
      onClose();
    };
    el.addEventListener('close', handle);
    return () => {
      el.removeEventListener('close', handle);
    };
  }, [onClose]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  const current = commands[Math.min(active, commands.length - 1)];
  // Groups in the order their first command appears: an id jump first, then pages, then
  // a visit search, then actions -- so Enter on "geo" opens Geography, not a search.
  const groups = [...new Set(commands.map((c) => c.group))].map((g) => ({
    group: g,
    items: commands.filter((c) => c.group === g),
  }));

  return (
    <dialog
      ref={ref}
      className="dialog palette"
      aria-label="Command palette"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="palette__search">
        <Icon.Search size={18} strokeWidth={1.75} aria-hidden="true" />
        <input
          ref={input}
          className="palette__input"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={current === undefined ? undefined : `${listId}-${current.id}`}
          aria-autocomplete="list"
          aria-label="Search pages and actions, or paste a visit or visitor id"
          placeholder="Search pages and actions, or paste an id…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              const step = event.key === 'ArrowDown' ? 1 : -1;
              setActive((a) => (a + step + commands.length) % Math.max(1, commands.length));
            } else if (event.key === 'Enter' && current !== undefined) {
              event.preventDefault();
              current.run();
            }
          }}
        />
        <Kbd>Esc</Kbd>
      </div>
      <div className="palette__list" id={listId} role="listbox" aria-label="Results">
        {groups.length === 0 && <p className="palette__empty">Nothing matches “{query}”.</p>}
        {groups.map(({ group, items }) => (
          <div key={group} role="group" aria-label={group}>
            <p className="menu__label" aria-hidden="true">
              {group}
            </p>
            {items.map((c) => {
              const Glyph = Icon[c.icon];
              const selected = c === current;
              return (
                <div
                  key={c.id}
                  id={`${listId}-${c.id}`}
                  role="option"
                  aria-selected={selected}
                  className="palette__item"
                  onMouseEnter={() => {
                    setActive(commands.indexOf(c));
                  }}
                  onClick={() => {
                    c.run();
                  }}
                >
                  <Glyph size={16} strokeWidth={1.75} aria-hidden="true" />
                  <span className="palette__label">{c.label}</span>
                  {c.hint !== undefined && <span className="menu__hint">{c.hint}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </dialog>
  );
}

function matches(text: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  return q === '' || text.toLowerCase().includes(q);
}

function useCommands(query: string, close: () => void): readonly Command[] {
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const { me, onSignedOut } = useSession();
  const { switchTo } = useThemeSwitch();
  const filters = parseFilters(search);
  const q = query.trim();

  return useMemo(() => {
    const go = (path: string, filtered: boolean): void => {
      close();
      void navigate({ pathname: path, search: filtered ? location.search : '' });
    };
    const write = (next: typeof filters): void => {
      close();
      void navigate({ pathname: location.pathname, search: serializeFilters(next).toString() });
    };
    const list: Command[] = [];

    if (UUID.test(q)) {
      list.push({
        id: 'open-visit',
        group: 'Find',
        label: `Open visit ${q}`,
        icon: 'Visits',
        run: () => {
          go(`/visits/${q.toLowerCase()}`, false);
        },
      });
    } else if (VISITOR.test(q)) {
      list.push({
        id: 'open-visitor',
        group: 'Find',
        label: `Open visitor ${q}`,
        icon: 'Visits',
        run: () => {
          go(`/visitors/${q.toLowerCase()}`, false);
        },
      });
    }
    for (const item of ALL_ITEMS) {
      if (matches(`${item.label} ${item.hint}`, q)) {
        list.push({
          id: `go-${item.key}`,
          group: 'Go to',
          label: item.label,
          hint: `g ${item.key}`,
          icon: item.icon,
          run: () => {
            go(item.to, item.filtered);
          },
        });
      }
    }

    if (q !== '') {
      list.push({
        id: 'search-visits',
        group: 'Find',
        label: `Search visits for “${q}”`,
        hint: 'ISP, city, browser, link',
        icon: 'Search',
        run: () => {
          close();
          void navigate({
            pathname: '/visits',
            search: serializeFilters(setScalar(filters, 'search', q)).toString(),
          });
        },
      });
    }

    const actions: Command[] = [
      ...THEMES.map((t): Command => ({
        id: `theme-${t.name}`,
        group: 'Actions',
        label: `Switch to ${t.label.toLowerCase()} theme`,
        icon: 'Theme',
        run: () => {
          switchTo(t.name);
          close();
        },
      })),
      {
        id: 'copy-link',
        group: 'Actions',
        label: 'Copy link to this view',
        icon: 'Copy',
        run: () => {
          void copyText(window.location.href);
          close();
        },
      },
      {
        id: 'automated',
        group: 'Actions',
        label:
          filters.scalars.include_automated === 'true'
            ? 'Exclude automated traffic'
            : 'Include automated traffic',
        icon: 'Filter',
        run: () => {
          write(
            setScalar(
              filters,
              'include_automated',
              filters.scalars.include_automated === 'true' ? '' : 'true',
            ),
          );
        },
      },
      {
        id: 'clear',
        group: 'Actions',
        label: 'Clear filters',
        icon: 'Close',
        run: () => {
          write(clearAll(filters));
        },
      },
      {
        id: 'sign-out',
        group: 'Actions',
        label: 'Sign out',
        icon: 'SignOut',
        run: () => {
          close();
          void logout(me.csrf_token).then(() => {
            onSignedOut();
          });
        },
      },
    ];
    list.push(...actions.filter((a) => matches(a.label, q)));
    return list;
  }, [
    q,
    filters,
    location.pathname,
    location.search,
    navigate,
    close,
    switchTo,
    me.csrf_token,
    onSignedOut,
  ]);
}
