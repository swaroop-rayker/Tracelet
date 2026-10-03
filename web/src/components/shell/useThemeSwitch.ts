/**
 * Switching theme from anywhere (the user menu, the command palette): applied at once,
 * persisted per admin (`PATCH /auth/me/preferences`, F9.AC16), with the M5 failure message
 * when it cannot be saved.
 */

import { useState } from 'react';
import { updatePreferences } from '@/api/auth';
import { useSession } from '@/session';
import { applyTheme, type ThemeName } from '@/theme';

export function useThemeSwitch(): {
  readonly switchTo: (theme: ThemeName) => void;
  readonly error: string | null;
} {
  const { me, setMe } = useSession();
  const [error, setError] = useState<string | null>(null);
  return {
    error,
    switchTo: (theme) => {
      applyTheme(theme);
      setError(null);
      void updatePreferences(me.csrf_token, { theme }).then((result) => {
        if (result.ok) setMe(result.data);
        else setError('Theme not saved: it will reset when you sign in again.');
      });
    },
  };
}
