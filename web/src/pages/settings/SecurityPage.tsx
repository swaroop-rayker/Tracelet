/**
 * Settings › Security (DESIGN §10.8): password, two-factor, recovery codes and the Telegram
 * recovery chat. Every request is one M1's Account page already sent; what changed is the
 * presentation: one card per control, forms in dialogs, a confirmation before regenerating
 * codes (UI-16), and new codes in a dialog that stays until "I have saved these" (E25).
 *
 * The server is the judge of every rule (F8): the password checklist only shows what can be
 * seen as you type, and a refusal comes back under its field.
 */

import { useEffect, useState } from 'react';
import {
  changePassword,
  confirmTelegramVerification,
  regenerateRecoveryCodes,
  startTelegramVerification,
} from '@/api/auth';
import { fieldMessage, type ApiError } from '@/api/client';
import { PasswordRules } from '@/components/PasswordRules';
import { RecoveryCodes } from '@/components/RecoveryCodes';
import {
  Alert,
  Badge,
  Button,
  Card,
  Dialog,
  ErrorNotice,
  Field,
  SettingRow,
  toast,
} from '@/components/ui';
import { ConfirmDialog, ShownOnceDialog } from '@/pages/settings/dialogs';
import { useSession } from '@/session';

const CODE_COUNT = 10;
const LOW_CODES_WARNING = 3;
const MIN_PASSWORD = 12;

export default function SecurityPage(): React.JSX.Element {
  return (
    <div className="stack">
      <Card title="Sign-in" description="What you sign in with: both are required.">
        <PasswordRow />
        <TwoFactorRow />
      </Card>
      <Card
        title="Account recovery"
        description="How you get back in if you lose your password or your authenticator."
      >
        <RecoveryCodesRow />
        <TelegramRow />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password
// ---------------------------------------------------------------------------

function PasswordRow(): React.JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingRow
        title="Password"
        description={`At least ${String(MIN_PASSWORD)} characters. Changing it signs out every other session.`}
      >
        <Button
          variant="secondary"
          onClick={() => {
            setOpen(true);
          }}
        >
          Change password…
        </Button>
      </SettingRow>
      <PasswordDialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
      />
    </>
  );
}

function PasswordDialog({
  open,
  onClose,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me, onRefresh } = useSession();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [mismatch, setMismatch] = useState<string | null>(null);

  const reset = (): void => {
    setCurrent('');
    setNext('');
    setRepeat('');
    setError(null);
    setMismatch(null);
  };

  async function submit(): Promise<void> {
    setMismatch(null);
    if (next !== repeat) {
      setMismatch('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await changePassword(me.csrf_token, current, next);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    reset();
    onClose();
    toast('Password changed. Every other session was signed out.');
    onRefresh();
  }

  const fieldErrors = new Set(error?.fields.map((f) => f.field) ?? []);
  return (
    <Dialog
      open={open}
      size="sm"
      title="Change password"
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
        <Field
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={setCurrent}
          revealable
          autoFocus
          error={fieldMessage(error, 'current_password')}
        />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={setNext}
          revealable
          error={fieldMessage(error, 'new_password')}
          after={<PasswordRules password={next} repeat={repeat} />}
        />
        <Field
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={setRepeat}
          revealable
          error={mismatch}
        />
        {error !== null && fieldErrors.size === 0 && <ErrorNotice error={error} />}
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
          <Button variant="primary" type="submit" busy={busy} busyLabel="Changing…">
            Change password
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Two-factor
// ---------------------------------------------------------------------------

function TwoFactorRow(): React.JSX.Element {
  const { me } = useSession();
  return (
    <SettingRow
      title="Authenticator app"
      description="Every admin signs in with a code from an authenticator app. It is required, so it cannot be turned off."
    >
      {me.totp_enrolled ? (
        <Badge tone="ok">Enabled</Badge>
      ) : (
        <Badge tone="warn">Not enrolled</Badge>
      )}
    </SettingRow>
  );
}

// ---------------------------------------------------------------------------
// Recovery codes
// ---------------------------------------------------------------------------

function RecoveryCodesRow(): React.JSX.Element {
  const { me, onRefresh } = useSession();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [fresh, setFresh] = useState<readonly string[] | null>(null);
  const left = me.recovery_codes_remaining;

  return (
    <>
      {left <= LOW_CODES_WARNING && (
        <Alert tone="warn" title={`${String(left)} left`}>
          Regenerate now, rather than when you need one.
        </Alert>
      )}
      <SettingRow
        title="Recovery codes"
        description={
          <>
            {String(left)} of {String(CODE_COUNT)} unused. Each signs you in once, without your
            authenticator.
            <span className="code-meter" aria-hidden="true">
              {Array.from({ length: CODE_COUNT }, (_, i) => (
                <span key={i} className={i < left ? 'is-left' : undefined} />
              ))}
            </span>
          </>
        }
      >
        <Button
          variant="danger"
          onClick={() => {
            setError(null);
            setConfirming(true);
          }}
        >
          Regenerate…
        </Button>
      </SettingRow>

      <ConfirmDialog
        open={confirming}
        title="Regenerate recovery codes?"
        confirmLabel="Regenerate"
        busyLabel="Generating…"
        busy={busy}
        error={error}
        onClose={() => {
          setConfirming(false);
        }}
        onConfirm={() => {
          setBusy(true);
          setError(null);
          void regenerateRecoveryCodes(me.csrf_token).then((result) => {
            setBusy(false);
            if (!result.ok) {
              setError(result.error);
              return;
            }
            setConfirming(false);
            setFresh(result.data);
            onRefresh();
          });
        }}
      >
        Your {String(left)} unused code{left === 1 ? '' : 's'} stop working immediately. You will be
        shown ten new ones, once.
      </ConfirmDialog>

      <ShownOnceDialog
        open={fresh !== null}
        title="Your new recovery codes"
        warning="The previous set no longer works. These are shown only now: save them before you close this."
        savedLabel="I have saved these codes"
        onDone={() => {
          setFresh(null);
          toast('New recovery codes saved.');
        }}
      >
        {fresh !== null && <RecoveryCodes codes={fresh} />}
      </ShownOnceDialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Telegram
// ---------------------------------------------------------------------------

function TelegramRow(): React.JSX.Element {
  const { me } = useSession();
  const [open, setOpen] = useState(false);
  return (
    <>
      <SettingRow
        title="Telegram recovery chat"
        description={
          me.telegram_verified
            ? 'Password reset links are sent to your verified chat. Verifying another replaces it.'
            : 'Not set up. Without it you cannot reset your password from the sign-in page; your recovery codes and the server CLI remain.'
        }
      >
        <span className="setting-row__actions">
          {me.telegram_verified ? (
            <Badge tone="ok">Verified</Badge>
          ) : (
            <Badge tone="warn">Not verified</Badge>
          )}
          <Button
            variant="secondary"
            onClick={() => {
              setOpen(true);
            }}
          >
            {me.telegram_verified ? 'Change chat…' : 'Set up…'}
          </Button>
        </span>
      </SettingRow>
      <TelegramDialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
      />
    </>
  );
}

function TelegramDialog({
  open,
  onClose,
}: {
  readonly open: boolean;
  readonly onClose: () => void;
}): React.JSX.Element {
  const { me, onRefresh } = useSession();
  const [chatId, setChatId] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  // A new step replaces the form, and the focused button with it: put focus on its field.
  useEffect(() => {
    if (open) document.querySelector<HTMLElement>('dialog[open] [data-autofocus]')?.focus();
  }, [step, open]);

  const close = (): void => {
    setChatId('');
    setCode('');
    setStep(1);
    setError(null);
    setInvalid(null);
    onClose();
  };

  async function send(): Promise<void> {
    setInvalid(null);
    const parsed = Number.parseInt(chatId.trim(), 10);
    if (!Number.isFinite(parsed) || chatId.trim().length === 0) {
      setInvalid('A chat id is a number. Ask @userinfobot in Telegram for yours.');
      return;
    }
    setBusy(true);
    setError(null);
    const result = await startTelegramVerification(me.csrf_token, parsed);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setStep(2);
  }

  async function verify(): Promise<void> {
    setBusy(true);
    setError(null);
    const result = await confirmTelegramVerification(me.csrf_token, code);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    close();
    toast('Telegram chat verified for password recovery.');
    onRefresh();
  }

  return (
    <Dialog
      open={open}
      size="sm"
      title="Telegram recovery chat"
      dismissible={false}
      onClose={close}
    >
      <p className="t-meta m-0 step-indicator">Step {String(step)} of 2</p>
      {step === 1 ? (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          <Field
            label="Telegram chat id"
            value={chatId}
            onChange={setChatId}
            inputMode="numeric"
            autoFocus
            hint="Message the bot first, then ask @userinfobot for your numeric id."
            error={invalid}
          />
          {error !== null && <ErrorNotice error={error} />}
          <div className="dialog__actions">
            <Button variant="ghost" disabled={busy} onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" busy={busy} busyLabel="Sending a code…">
              Send a code
            </Button>
          </div>
        </form>
      ) : (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            void verify();
          }}
        >
          <p className="t-secondary m-0">
            Enter the six-digit code the bot just sent to that chat.
          </p>
          <Field
            label="Verification code"
            value={code}
            onChange={setCode}
            inputMode="numeric"
            autoComplete="one-time-code"
            mono
            maxLength={10}
            autoFocus
            error={fieldMessage(error, 'code')}
          />
          {error !== null && fieldMessage(error, 'code') === null && <ErrorNotice error={error} />}
          <div className="dialog__actions">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setStep(1);
                setError(null);
              }}
            >
              Use a different chat
            </Button>
            <Button variant="primary" type="submit" busy={busy} busyLabel="Verifying…">
              Verify this chat
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
