# Tracelet — Account and settings redesign plan

**Status:** **Approved 2026-10-06** and moved into DESIGN §10.8, §10.9 and §12 E23–E28 and
MILESTONES M5.6, which now govern it. Presentation only: no business-logic, endpoint, rule,
role or schema change.
**Author:** repository owner, with Claude
**Shared copy:** https://claude.ai/code/artifact/0fb360e8-486f-4a0a-ad3e-5c13fbcbe865

---

## Summary

The one long Account page becomes a **Settings area**: a sub-navigation and six focused
pages, each a card of setting rows. It reuses only endpoints that already exist: no
endpoint, rule, role, message wording or database change. About 4.5 days, plus 1.5
optional.

- **What changes:** structure (one page becomes six), layout (the DESIGN §10.8 setting
  rows, finally built), and interaction polish (confirmations, inline feedback, relative
  times, readable states).
- **What does not:** every request the page sends, every server rule (12-character
  passwords, codes shown once, owner-only routes, enumeration-resistant messages), and
  every step order in sign-in, enrolment and recovery.
- **Two existing endpoints get a UI they never had:** `PATCH /auth/me/preferences` for the
  display time zone (only the theme was wired), and `/admins` for owner actions (invite,
  change role, disable, reissue a link). Both are already shipped, tested and audited, so
  the UI adds no behaviour.

**Status: a plan, not yet approved.** Nothing changes in the repository until the owner
approves. On approval, DESIGN §10.8 and §10.9 are updated with the new layouts in the same
change as the code.

## Today

The Account page is still M1's layout restyled: seven stacked sections, about three screens
long, mixing your own security, team management and server health. DESIGN §10.8 already
called for a sub-navigation with setting rows. M5.5 restyled the page but did not rebuild
it.

| Area | Today (`AccountPage.tsx`) | Problem |
| --- | --- | --- |
| Structure | Account, Password, Recovery codes, Telegram, Sessions, Admins, System readiness on one page | Nothing can be linked to directly (for example, "go to Sessions"). Unrelated jobs share one scroll |
| Account summary | A `dl` of raw values (`owner`, `active`), a "Refresh" button, timestamps via `toLocaleString()` | Reads like a debug dump. No relative times (DESIGN E11) |
| Password | Three fields and a submit; success shows as a callout above the form | No show/hide toggle. The rule is a hint, not a checklist. Success can scroll out of view |
| Recovery codes | "Regenerate" runs on one click | It invalidates all ten codes at once with no confirmation, contrary to DESIGN UI-16 for destructive actions |
| Telegram | Chat id form, then a code form that replaces it | Correct flow, but no sense of step one of two. The verified state is a bare callout |
| Sessions | One card per session: "This device" or "Another device", the IP prefix, absolute times. Revoke on one click | No confirmation. Signing yourself out is the same button, worded differently. No way to sign out all other sessions at once |
| Admins (owner) | A read-only list | Stale copy says the management screen "arrives with the dashboard in M5", which shipped without it |
| System readiness | Database, PostGIS and migration checks | Server health on a personal page. It belongs with M7's Health page |
| Time zone | Shown, never editable | The preferences endpoint already accepts it; only the theme was wired |
| Controls | Plain `<button>` elements under M1's legacy styles (ERRORS E42) | Not the Button primitive, so sizes, focus and busy states differ from the rest of the app |
| Sign-in pages | Login, enrolment and recovery on the 400 px auth card (DESIGN §10.9) | Mostly fine. Enrolment shows the TOTP secret as text only, with no QR code |

## New structure

Settings stays personal plus team: six pages under `/settings`, with the old `/account`
redirecting so no bookmark breaks.

```
Six settings pages replace one long Account page

                     ┌───────────────────────────────────────────────────────────────────┐
                ┌────│ Profile                                         /settings/profile │
                │    │ Name, email, role and status, read-only; when this session expires│
                │    └───────────────────────────────────────────────────────────────────┘
                │    ┌───────────────────────────────────────────────────────────────────┐
                ├────│ Preferences                                 /settings/preferences │
                │    │ Theme and display time zone; the reporting zone shown read-only   │
┌────────────┐  │    └───────────────────────────────────────────────────────────────────┘
│  Settings  │  │    ┌───────────────────────────────────────────────────────────────────┐
│ replaces   │──┼────│ Security                                       /settings/security │
│ /account   │  │    │ Password, two-factor status, recovery codes, Telegram recovery    │
└────────────┘  │    └───────────────────────────────────────────────────────────────────┘
                │    ┌───────────────────────────────────────────────────────────────────┐
                ├────│ Sessions                                       /settings/sessions │
                │    │ Every signed-in device: revoke one, or sign out all others        │
                │    └───────────────────────────────────────────────────────────────────┘
                │    ┌───────────────────────────────────────────────────────────────────┐
                ├────│ Team (owner only)                                  /settings/team │
                │    │ Admins: invite, change role, disable, reissue a setup link, delete│
                │    └───────────────────────────────────────────────────────────────────┘
                │    ┌───────────────────────────────────────────────────────────────────┐
                └────│ System                                           /settings/system │
                     │ Readiness checks, until M7 moves them to the Health page          │
                     └───────────────────────────────────────────────────────────────────┘
```

- **Navigation:** at 1024 px and wider, a left sub-navigation inside the page. Under
  1024 px, a select at the top of the page. The sidebar entry "Account & security" becomes
  "Settings". The user menu and the command palette link straight to each page.
- **Not in Settings:** M6's Geofences and Alerts, and M7's Links, Retention and Backups,
  belong in the sidebar's Configure group (DESIGN §16), so Settings never becomes a second
  dashboard.

## Screens

Every page has the same frame: the sub-navigation on the left, a page header, then cards of
setting rows. Each row has a label and one line of explanation on the left, and its state
or action on the right.

```
Security page: one card per control, actions on the right (wireframe at 1440 px;
the dashboard sidebar and header are left out)

┌───────────────┬──────────────────────────────────────────────────────────────────────┐
│ Profile       │ Security                                                             │
│ Preferences   │ Password, two-factor, recovery codes and Telegram                    │
│▐Security▌     │ ┌──────────────────────────────────────────────────────────────────┐ │
│ Sessions      │ │ Password                                    [Change password…]   │ │
│ Team          │ │ At least 12 characters.                                          │ │
│ System        │ │ Changing it signs out your other sessions.                       │ │
│               │ └──────────────────────────────────────────────────────────────────┘ │
│               │ ┌──────────────────────────────────────────────────────────────────┐ │
│               │ │ Two-factor authentication                            ( Enabled ) │ │
│               │ │ An authenticator app is required for every admin.                │ │
│               │ └──────────────────────────────────────────────────────────────────┘ │
│               │ ┌──────────────────────────────────────────────────────────────────┐ │
│               │ │ Recovery codes                              [Regenerate…] danger │ │
│               │ │ 7 of 10 unused; regenerating replaces all ten                    │ │
│               │ │ ■ ■ ■ ■ ■ ■ ■ □ □ □                                              │ │
│               │ └──────────────────────────────────────────────────────────────────┘ │
│               │ ┌──────────────────────────────────────────────────────────────────┐ │
│               │ │ Telegram recovery chat ( Verified )         [Change chat…]       │ │
│               │ │ Password reset links are sent to this chat.                      │ │
│               │ └──────────────────────────────────────────────────────────────────┘ │
└───────────────┴──────────────────────────────────────────────────────────────────────┘
```

A destructive action (Regenerate) is outlined in the error colour and always asks first
(see Interaction patterns). A form opens in a small dialog, so the page itself stays a calm
summary.

| Page | Rows | Calls it makes (all existing) |
| --- | --- | --- |
| **Profile** | Avatar initials, name, email. Role and status as badges. "This session expires in 9 h" | `GET /auth/me` |
| **Preferences** | Theme as a three-way choice with previews (Semi-dark, Light, Dark). Display time zone as a searchable list. Reporting zone, read-only, with a note on what it governs | `PATCH /auth/me/preferences` (`theme`, `timezone`) |
| **Security** | Password (the change form in a dialog, with show/hide and the 12-character rule as a live checklist). Two-factor status. Recovery codes (count, 10-step meter, Regenerate). Telegram chat (a two-step dialog: send code, then verify) | `POST /auth/password`, `POST /auth/totp/regenerate-codes`, `/auth/telegram/verify/start` and `/confirm` |
| **Sessions** | A table: device (a "This device" badge), IP prefix, started, last seen (relative), expires. Revoke per row. "Sign out all other sessions" | `GET /auth/sessions`, `DELETE /auth/sessions/{id}` (one call per session) |
| **Team** (owner) | A table: name, email, role badge, status, two-factor, last sign-in, locked. An Invite dialog that shows the one-time setup link with Copy. A row menu: change role, disable or enable, new setup link, delete | `/admins` (GET, POST, PATCH, DELETE), `/admins/{id}/enrollment-token` |
| **System** | Ready or not ready, then one row per check with its detail, and a Re-check button | `GET /readyz` |

**States on every page:** a skeleton while loading, an error with the trace id, an empty
state with its reason, then the data (UI rules, four states). Under 1024 px, cards stack,
actions move under their text, and tables become row cards (DESIGN §9.4).

## Interaction patterns

Nine patterns cover every control in Settings. All but one use primitives that already
exist: Card, Badge, Button, Dialog, DataTable, Menu, SegmentedControl, Select, Secret and
Timestamp. The one new primitive is a Toast.

| Pattern | Rule | Used by |
| --- | --- | --- |
| **Setting row** | Label and one line of explanation on the left; state (Badge) or action (Button) on the right. At most one primary button per card | Every page |
| **Confirm before destroying** (UI-16) | A danger button and a dialog that names the object and the consequence ("Your 7 unused codes stop working immediately"). Deleting an admin requires typing their email | Regenerate codes, revoke a session, sign out all others, disable or delete an admin |
| **Shown once** | New recovery codes and an invite's setup link open in a dialog that a backdrop click cannot close. Copy all and Download .txt (E10); Done is enabled only after "I have saved these" is ticked | Regenerate codes, Invite |
| **Forms in dialogs** | Small dialog, focus on the first field, Enter submits, Escape cancels, focus returns to the button that opened it | Password, Telegram, Invite, change role |
| **Validation** | The browser checks only what it checks today (repeat matches, chat id is a number). The 12-character rule shows as a live checklist, but the server remains the judge and its field errors show under the field | Password, Telegram |
| **Feedback** | Success shows in the row itself (a badge or new value) and as a Toast in a polite live region. Errors stay inside the dialog with their trace id | Every action |
| **Busy** | The button shows its busy label and blocks a second submit; the rest of the page stays usable | Every action |
| **Time** | Relative time with the absolute time on hover (E11), in your display time zone | Sessions, Team, Profile |
| **Role-aware** (UI-17) | Team is hidden from analysts (a pure configuration page, which UI-17 allows), and the server still refuses them | Team |

## Sign-in, enrolment and recovery

The same steps, in the same order, with the same messages, on the existing 400 px auth card
(DESIGN §10.9). The work is clarity and finish, about 1 day, plus 0.5 day for an optional
QR code.

- **Step indicator:** "Step 1 of 2" on sign-in (password, then code), and "1 Password ·
  2 Authenticator · 3 Recovery codes" on enrolment, so a person always knows how far there
  is to go.
- **Password fields:** a show/hide toggle; on enrolment and reset, the same live
  12-character checklist as Settings.
- **Code fields:** one input in a monospace display, `inputmode="numeric"` and
  `autocomplete="one-time-code"`. Recovery codes are shown in groups for readability; what
  is sent is unchanged.
- **TOTP secret:** shown in groups of four for typing. Copy copies the raw secret. "Open in
  an authenticator app on this device" stays.
- **Optional QR code** for the `otpauth://` URI, drawn in the browser so the secret never
  leaves the page. It needs a small library, so it needs an ADR and a ledger entry. It is
  shared with the enhancement plan's link builder (D4), so one ADR covers both.
- **Footer links:** "Use a recovery code" and "Forgot your password?" as quiet links under
  the card, as today.

**Wording that must not change:** the identical response for an existing and a non-existing
account (F8.AC10), and "invalid code" for a replayed code. A friendlier message per case
would undo enumeration resistance (API §4.2).

## Guardrails

The redesign is presentation only. Review checks each of these, and the first is checked
by machine.

- **No server change.** `git diff` touches only `web/` and `docs/`; no migration;
  `openapi-check` clean; the generated client unchanged.
- **The server stays the judge.** Client checks (passwords match, the 12-character
  checklist, numeric chat id) are hints. A server refusal always wins and shows under its
  field.
- **Owner-only stays server-enforced (F8.AC12, invariant 9).** Hiding Team from analysts is
  a courtesy; every `/admins` route still refuses them, and every owner action still
  writes `audit_log`.
- **Consequences stated, not changed.** The dialogs name what the server already does: a
  password change signs out other sessions; disabling an admin revokes their sessions at
  once; the last active owner cannot be demoted or disabled (`409 LAST_OWNER`, shown
  inline).
- **Secrets stay ephemeral.** Recovery codes and setup links live in component state only:
  never in storage, the URL or a log, and cleared when their dialog closes.
- **Enumeration resistance (F8.AC10).** Sign-in, reset and recovery responses keep their
  identical wording for existing and unknown accounts.
- **CSRF unchanged.** Every state-changing call sends `X-CSRF-Token` as today, read from
  `/auth/me`.
- **No new dependency**, except the optional QR library behind its own ADR. No inline
  styles; zero CSP violations in the three-engine sweep.

## Build phases

Six core phases come to 4.5 days; Team and the QR code add 1.5 days if approved. Each
phase ends green on `./scripts/tl verify` and lands as its own commit.

| # | Phase | Done when | Effort |
| --- | --- | --- | --- |
| 1 | **Shell:** `/settings` layout, sub-navigation (a select under 1024 px), `/account` redirect, sidebar, user-menu and palette entries, the Toast primitive with a state test | Every sub-route renders inside the frame. Old links land on Profile. Toast announces in a polite live region | 0.75 d |
| 2 | **Profile and Preferences:** time zones from the browser's `Intl.supportedValuesOf('timeZone')`, so no list is shipped | A theme or zone change applies at once, survives reload, and shows a Toast | 0.5 d |
| 3 | **Security:** password dialog, recovery codes with confirm and the shown-once dialog, two-step Telegram dialog | Same requests as today, verified in the network log. Regenerate cannot run without confirming | 1 d |
| 4 | **Sessions and System** | Sign out all others revokes every other row and keeps this one. Revoking this device signs out as today | 0.5 d |
| 5 | **Sign-in, enrolment, recovery** polish | Same steps and messages. Enumeration responses compared word for word before and after | 1 d |
| 6 | **QA and docs** | Zero CSP violations in Chromium, Firefox and WebKit. Screenshots of 6 pages and the auth pages × 3 themes × 3 widths. A keyboard pass on every dialog. DESIGN §10.8, §10.9 and §5 (Toast) updated | 0.75 d |
| 7 | *Optional:* **Team** (owner) | Invite shows the setup link once. Role, status, new link and delete work. `409 LAST_OWNER` shows inline. Each action appears in `audit_log` | 1 d |
| 8 | *Optional:* **QR code** at enrolment | ADR accepted, ledger row with gzipped size, CSP sweep clean | 0.5 d |

## Decisions needed

Tick what goes ahead; nothing is built until then.

- [x] **Structure:** six pages under `/settings`, with `/account` redirecting
- [x] **Team now or later:** build owner management now (phase 7), or keep today's
      read-only list in the new layout until M7 — *now*
- [x] **QR code at enrolment** (phase 8): accept a small QR library, under one ADR shared
      with the link builder — *not now: no new dependency*
- [x] **Saved-gate:** require "I have saved these" before a new set of codes or a setup
      link can be closed — *yes*
- [x] **Where it ships:** together with the enhancement plan's Phase A as one pre-M6 PR, or
      on its own — *with Phase A, on `feat/m5.6-ui-enhancements`*

Approval changes DESIGN only (§10.8 and §10.9 layouts, §5 for the Toast, §12 for the new
entries) and adds a MILESTONES row. No SPEC change: every requirement stays as written. A
QR library would take the next ADR number, ADR-0021.
