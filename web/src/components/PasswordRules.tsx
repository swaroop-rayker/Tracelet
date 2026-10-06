/**
 * The live password checklist (DESIGN §10.8, §10.9, §12 E28), shared by Settings › Security,
 * enrolment and the password reset. It shows only what can be seen while typing; the server
 * (`auth/service.py validate_password`) is the judge, and checks the rest when you save.
 */

import { Checklist } from '@/components/ui';

/** The server's MIN_PASSWORD_LENGTH; a hint here, enforced there. */
const MIN_PASSWORD = 12;

export function PasswordRules({
  password,
  repeat,
}: {
  readonly password: string;
  readonly repeat: string;
}): React.JSX.Element {
  return (
    <div className="stack-sm">
      <Checklist
        label="Password rules"
        items={[
          {
            key: 'length',
            text: `At least ${String(MIN_PASSWORD)} characters`,
            met: password.length >= MIN_PASSWORD,
          },
          { key: 'repeat', text: 'Repeated exactly', met: password !== '' && password === repeat },
        ]}
      />
      <p className="t-meta m-0">
        It must also not contain your account name or the site name, or be a common password. That
        is checked when you save.
      </p>
    </div>
  );
}
