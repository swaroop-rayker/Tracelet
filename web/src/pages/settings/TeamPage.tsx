/**
 * Settings › Team (DESIGN §10.8, §12 E27): the owner manages the admins -- invite, change a
 * role, disable or enable, issue a new setup link, delete -- through the `/admins` routes M1
 * shipped (API §5). Every one is owner-only on the server and writes `audit_log` (CLAUDE.md
 * invariant 9). This page adds no rule: the last-owner (`409 LAST_OWNER`) and self-delete
 * (`422`) refusals come back from the server and are shown where they happened.
 *
 * Courtesies, not controls: an analyst sees an explanation instead of the list (UI-17), your
 * own row offers no actions, and a new setup link is offered only while an admin's setup is
 * still pending. There is no way to set another admin's password, by design (API §5).
 */

import { useCallback, useEffect, useState } from 'react';
import {
  createAdmin,
  deleteAdmin,
  fetchAdmins,
  issueSetupLink,
  updateAdmin,
  type AdminRole,
  type AdminSummary,
  type SetupLink,
} from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import {
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  Menu,
  MenuItem,
  Secret,
  SegmentedControl,
  Timestamp,
  toast,
} from '@/components/ui';
import { ConfirmDialog, ShownOnceDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

type Load =
  | { readonly phase: 'loading' }
  | { readonly phase: 'error'; readonly error: ApiError }
  | { readonly phase: 'ready'; readonly data: readonly AdminSummary[] };

type Action =
  | { readonly kind: 'role'; readonly admin: AdminSummary; readonly to: AdminRole }
  | {
      readonly kind: 'status';
      readonly admin: AdminSummary;
      readonly to: 'active' | 'disabled';
    }
  | { readonly kind: 'delete'; readonly admin: AdminSummary };

const STATUS = {
  active: { tone: 'ok', text: 'Active' },
  pending_enrollment: { tone: 'warn', text: 'Setup pending' },
  disabled: { tone: 'error', text: 'Disabled' },
} as const;

function roleName(role: AdminRole): string {
  return role === 'owner' ? 'Owner' : 'Analyst';
}

export default function TeamPage(): React.JSX.Element {
  const { me } = useSession();
  if (me.role !== 'owner') {
    return (
      <Card>
        <EmptyState
          title="Only an owner manages the team"
          reason="Ask an owner to invite an admin, change a role or remove someone."
        />
      </Card>
    );
  }
  return <Team />;
}

function Team(): React.JSX.Element {
  const { me } = useSession();
  const [state, setState] = useState<Load>({ phase: 'loading' });
  const [action, setAction] = useState<Action | null>(null);
  const [inviting, setInviting] = useState(false);
  const [link, setLink] = useState<{ readonly who: string; readonly link: SetupLink } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (): Promise<void> => {
    const result = await fetchAdmins();
    setState(
      result.ok ? { phase: 'ready', data: result.data } : { phase: 'error', error: result.error },
    );
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(): Promise<void> {
    if (action === null) return;
    setBusy(true);
    setError(null);
    const { admin } = action;
    const result =
      action.kind === 'delete'
        ? await deleteAdmin(me.csrf_token, admin.id)
        : action.kind === 'role'
          ? await updateAdmin(me.csrf_token, admin.id, { role: action.to })
          : await updateAdmin(me.csrf_token, admin.id, { status: action.to });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setAction(null);
    toast(
      action.kind === 'delete'
        ? `${admin.display_name} was removed.`
        : action.kind === 'role'
          ? `${admin.display_name} is now an ${action.to === 'owner' ? 'owner' : 'analyst'}.`
          : `${admin.display_name} is ${action.to === 'active' ? 'enabled' : 'disabled'}.`,
    );
    void load();
  }

  async function newLink(admin: AdminSummary): Promise<void> {
    const result = await issueSetupLink(me.csrf_token, admin.id);
    if (!result.ok) {
      setState({ phase: 'error', error: result.error });
      return;
    }
    setLink({ who: admin.display_name, link: result.data });
  }

  return (
    <Card
      title="Admins"
      description="Everyone who can sign in to this dashboard. Every change here is audited."
      actions={
        <Button
          variant="primary"
          icon="Invite"
          onClick={() => {
            setInviting(true);
          }}
        >
          Invite…
        </Button>
      }
    >
      {state.phase === 'loading' && <Loading kind="table" label="Loading admins…" />}
      {state.phase === 'error' && <ErrorNotice error={state.error} />}
      {state.phase === 'ready' && (
        <DataTable
          caption="Admins"
          rowKey={(a) => a.id}
          rows={state.data}
          columns={[
            {
              key: 'who',
              header: 'Admin',
              render: (a) => (
                <span className="link-cell">
                  <span>
                    {a.display_name}
                    {a.id === me.id && (
                      <>
                        {' '}
                        <Badge>You</Badge>
                      </>
                    )}
                  </span>
                  <span className="t-meta t-mono">{a.email}</span>
                </span>
              ),
            },
            {
              key: 'role',
              header: 'Role',
              render: (a) => <Badge tone="info">{roleName(a.role)}</Badge>,
            },
            {
              key: 'status',
              header: 'Status',
              render: (a) => (
                <span className="badges">
                  <Badge tone={STATUS[a.status].tone}>{STATUS[a.status].text}</Badge>
                  {a.locked_until !== null && <Badge tone="warn">Locked</Badge>}
                </span>
              ),
            },
            {
              key: '2fa',
              header: 'Two-factor',
              render: (a) => (a.totp_enrolled ? 'Enrolled' : 'Not yet'),
            },
            {
              key: 'last',
              header: 'Last sign-in',
              render: (a) =>
                a.last_login_at === null ? (
                  <span className="muted">Never</span>
                ) : (
                  <Timestamp iso={a.last_login_at} zone={me.timezone} />
                ),
            },
            {
              key: 'actions',
              header: <span className="sr-only">Actions</span>,
              render: (a) =>
                a.id === me.id ? null : (
                  <Menu
                    label={`Actions for ${a.display_name}`}
                    icon="More"
                    iconOnly
                    size="sm"
                    align="end"
                  >
                    {(close) => (
                      <>
                        <MenuItem
                          onSelect={() => {
                            close();
                            setError(null);
                            setAction({
                              kind: 'role',
                              admin: a,
                              to: a.role === 'owner' ? 'analyst' : 'owner',
                            });
                          }}
                        >
                          {a.role === 'owner' ? 'Make analyst' : 'Make owner'}
                        </MenuItem>
                        {a.status === 'pending_enrollment' && (
                          <MenuItem
                            icon="Link"
                            onSelect={() => {
                              close();
                              void newLink(a);
                            }}
                          >
                            New setup link
                          </MenuItem>
                        )}
                        {a.status !== 'pending_enrollment' && (
                          <MenuItem
                            danger={a.status === 'active'}
                            onSelect={() => {
                              close();
                              setError(null);
                              setAction({
                                kind: 'status',
                                admin: a,
                                to: a.status === 'active' ? 'disabled' : 'active',
                              });
                            }}
                          >
                            {a.status === 'active' ? 'Disable' : 'Enable'}
                          </MenuItem>
                        )}
                        <MenuItem
                          danger
                          onSelect={() => {
                            close();
                            setError(null);
                            setAction({ kind: 'delete', admin: a });
                          }}
                        >
                          Delete…
                        </MenuItem>
                      </>
                    )}
                  </Menu>
                ),
            },
          ]}
        />
      )}

      <ConfirmDialog
        open={action !== null}
        title={
          action === null
            ? ''
            : action.kind === 'delete'
              ? `Delete ${action.admin.display_name}?`
              : action.kind === 'role'
                ? `Make ${action.admin.display_name} ${action.to === 'owner' ? 'an owner' : 'an analyst'}?`
                : `${action.to === 'active' ? 'Enable' : 'Disable'} ${action.admin.display_name}?`
        }
        confirmLabel={
          action === null
            ? ''
            : action.kind === 'delete'
              ? 'Delete'
              : action.kind === 'role'
                ? `Make ${action.to}`
                : action.to === 'active'
                  ? 'Enable'
                  : 'Disable'
        }
        danger={
          action !== null &&
          (action.kind === 'delete' || (action.kind === 'status' && action.to === 'disabled'))
        }
        typed={
          action?.kind === 'delete'
            ? { label: `Type ${action.admin.email} to confirm`, phrase: action.admin.email }
            : undefined
        }
        busy={busy}
        error={error}
        onClose={() => {
          setAction(null);
        }}
        onConfirm={() => {
          void run();
        }}
      >
        {action === null
          ? null
          : action.kind === 'delete'
            ? 'Their account and sessions are removed, so they can no longer sign in. This cannot be undone; to let them back, invite them again.'
            : action.kind === 'role'
              ? action.to === 'owner'
                ? 'Owners change configuration, manage the team and can see full IP addresses.'
                : 'Analysts see every analytics page but cannot change configuration or the team.'
              : action.to === 'disabled'
                ? 'They are signed out everywhere at once and cannot sign in until enabled again.'
                : 'They can sign in again with their own password and authenticator.'}
      </ConfirmDialog>

      <InviteDialog
        open={inviting}
        onClose={() => {
          setInviting(false);
        }}
        onInvited={(who, setup) => {
          setInviting(false);
          setLink({ who, link: setup });
          void load();
        }}
      />

      <ShownOnceDialog
        open={link !== null}
        title={link === null ? '' : `Setup link for ${link.who}`}
        warning="Send this to them yourself. It works once, and it is not shown again; anyone holding it can set this account's password."
        savedLabel="I have copied this link"
        onDone={() => {
          setLink(null);
          toast('Setup link issued.');
        }}
      >
        {link !== null && (
          <div className="stack-sm">
            <Secret label="One-time setup link" value={link.link.url} />
            <p className="t-meta m-0">
              Expires <Timestamp iso={link.link.expires_at} zone={me.timezone} />.
            </p>
          </div>
        )}
      </ShownOnceDialog>
    </Card>
  );
}

function InviteDialog({
  open,
  onClose,
  onInvited,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly onInvited: (who: string, link: SetupLink) => void;
}): React.JSX.Element {
  const { me } = useSession();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<AdminRole>('analyst');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const reset = (): void => {
    setEmail('');
    setName('');
    setRole('analyst');
    setError(null);
  };

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await createAdmin(me.csrf_token, {
      email: email.trim(),
      display_name: name.trim(),
      role,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    reset();
    onInvited(result.data.admin.display_name, result.data.link);
  }

  const fieldError = fieldMessage(error, 'email') ?? fieldMessage(error, 'display_name');
  return (
    <Dialog
      open={open}
      size="sm"
      title="Invite an admin"
      dismissible={false}
      onClose={() => {
        reset();
        onClose();
      }}
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <p className="t-secondary m-0">
          They get a one-time link to choose their own password and add an authenticator. No
          password is ever set for them.
        </p>
        <Field
          label="Email"
          type="email"
          autoComplete="off"
          value={email}
          onChange={setEmail}
          autoFocus
          error={fieldMessage(error, 'email')}
        />
        <Field
          label="Name"
          autoComplete="off"
          value={name}
          onChange={setName}
          maxLength={120}
          error={fieldMessage(error, 'display_name')}
        />
        <div className="field">
          <span className="field__label">Role</span>
          <SegmentedControl
            label="Role"
            value={role}
            onChange={(v) => {
              setRole(v === 'owner' ? 'owner' : 'analyst');
            }}
            options={[
              { value: 'analyst', label: 'Analyst' },
              { value: 'owner', label: 'Owner' },
            ]}
          />
        </div>
        {error !== null && fieldError === null && <ErrorNotice error={error} />}
        <div className="dialog__actions">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button variant="primary" type="submit" busy={busy} busyLabel="Inviting…">
            Invite
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
