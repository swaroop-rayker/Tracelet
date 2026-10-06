/**
 * Settings › Profile (DESIGN §10.8): who you are signed in as, and this session. Read-only:
 * an admin's name and role are changed by an owner on Team, never here.
 */

import { useState } from 'react';
import { Link } from 'react-router';
import { logout } from '@/api/auth';
import { Badge, Button, Card, SettingRow, Timestamp } from '@/components/ui';
import { label } from '@/format';
import { useSession } from '@/session';

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter((part) => part !== '')
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

const STATUS_TONE = { active: 'ok', pending_enrollment: 'warn', disabled: 'error' } as const;

export default function ProfilePage(): React.JSX.Element {
  const { me, onSignedOut } = useSession();
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack">
      <Card>
        <div className="profile-head">
          <span className="avatar avatar--lg" aria-hidden="true">
            {initials(me.display_name)}
          </span>
          <div className="profile-head__who">
            <p className="profile-head__name m-0">{me.display_name}</p>
            <p className="t-secondary t-mono m-0">{me.email}</p>
          </div>
          <span className="badges">
            <Badge tone="info">{me.role === 'owner' ? 'Owner' : 'Analyst'}</Badge>
            <Badge tone={STATUS_TONE[me.status]}>{label(me.status)}</Badge>
          </span>
        </div>
      </Card>
      <Card title="Account">
        <SettingRow
          title="Role"
          description={
            me.role === 'owner'
              ? 'Owners can change configuration, manage the team and see full IP addresses.'
              : 'Analysts see every analytics page; configuration and the team are for owners.'
          }
        >
          <Badge tone="info">{me.role === 'owner' ? 'Owner' : 'Analyst'}</Badge>
        </SettingRow>
        <SettingRow
          title="Name and email"
          description="Changed by an owner on the Team page, so a name is never self-asserted."
        />
      </Card>
      <Card title="This session">
        <SettingRow
          title="Signed in on this device"
          description={
            <>
              Expires <Timestamp iso={me.session_expires_at} zone={me.timezone} />. Other devices
              are on{' '}
              <Link className="link" to="/settings/sessions">
                Sessions
              </Link>
              .
            </>
          }
        >
          <Button
            variant="secondary"
            icon="SignOut"
            busy={busy}
            busyLabel="Signing out…"
            onClick={() => {
              setBusy(true);
              void logout(me.csrf_token).then(() => {
                onSignedOut();
              });
            }}
          >
            Sign out
          </Button>
        </SettingRow>
      </Card>
    </div>
  );
}
