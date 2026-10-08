/**
 * Settings › Preferences (DESIGN §10.8): the theme, the time zone times are *shown* in, and
 * how often System Health refreshes (F10.AC1). All through `PATCH /auth/me/preferences`.
 * Since M7.7 also your saved views (F9.AC26), through `/saved-views`.
 * The reporting zone -- where daily buckets are cut -- is the server's and is read-only here.
 */

import { useState } from 'react';
import { updatePreferences } from '@/api/auth';
import type { ApiError } from '@/api/client';
import { useThemeSwitch } from '@/components/shell/useThemeSwitch';
import {
  Alert,
  Card,
  ErrorNotice,
  SegmentedControl,
  Select,
  SettingRow,
  toast,
} from '@/components/ui';
import { SavedViewsCard } from '@/pages/settings/SavedViewsCard';
import { useSession } from '@/session';
import { THEMES, asTheme } from '@/theme';

/**
 * Browsers still list a few zones by their pre-rename names (Chromium: "Asia/Calcutta"). The
 * server and every other admin use the current IANA name, so the list does too.
 */
const RENAMED: Readonly<Record<string, string>> = {
  'Asia/Calcutta': 'Asia/Kolkata',
  'Asia/Katmandu': 'Asia/Kathmandu',
  'Asia/Rangoon': 'Asia/Yangon',
  'Asia/Saigon': 'Asia/Ho_Chi_Minh',
  'Europe/Kiev': 'Europe/Kyiv',
};

/**
 * Every IANA zone the browser knows, under current names, plus the saved and the reporting
 * zone, so neither can go missing from the control. Ships no list of its own.
 */
/** System Health's refresh choices (F10.AC1); the server accepts exactly these. */
const REFRESH_SECONDS = [5, 15, 30, 60] as const;

function zones(...always: readonly string[]): readonly string[] {
  const known =
    typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  return [...new Set([...known.map((z) => RENAMED[z] ?? z), ...always])].sort();
}

export default function PreferencesPage(): React.JSX.Element {
  const { me, setMe } = useSession();
  const { switchTo, error: themeError } = useThemeSwitch();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const save = (change: Parameters<typeof updatePreferences>[1], message: string): void => {
    setBusy(true);
    setError(null);
    void updatePreferences(me.csrf_token, change).then((result) => {
      setBusy(false);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setMe(result.data);
      toast(message);
    });
  };

  return (
    <div className="stack">
      <Card title="Appearance">
        <SettingRow
          title="Theme"
          description="Semi-dark is the default. Saved to your account, so it follows you."
        >
          <SegmentedControl
            label="Theme"
            value={asTheme(me.theme)}
            onChange={(value) => {
              const theme = asTheme(value);
              switchTo(theme);
              toast(`Theme: ${THEMES.find((t) => t.name === theme)?.label ?? value}`);
            }}
            options={THEMES.map((t) => ({ value: t.name, label: t.label }))}
          />
        </SettingRow>
        {themeError !== null && <Alert tone="error" title={themeError} />}
      </Card>

      <Card title="Time">
        <SettingRow
          title="Display time zone"
          description="Times in lists, details and timestamps are shown in this zone."
        >
          <Select
            label="Display time zone"
            hideLabel
            value={me.timezone}
            onChange={(timezone) => {
              save({ timezone }, `Times are now shown in ${timezone}`);
            }}
            options={zones(me.timezone, me.reporting_tz).map((z) => ({
              value: z,
              label: z.replaceAll('_', ' '),
            }))}
          />
        </SettingRow>
        <SettingRow
          title="Reporting time zone"
          description="Charts, daily figures and the hour heatmap are cut in this zone. It is set on the server, so every admin sees the same days."
        >
          <span className="t-mono">{me.reporting_tz}</span>
        </SettingRow>
      </Card>

      <Card title="System health">
        <SettingRow
          title="Refresh every"
          description="How often host figures and degraded conditions update while the page is open. Polling pauses while the tab is hidden."
        >
          <SegmentedControl
            label="Refresh every"
            value={String(me.health_refresh_seconds)}
            onChange={(value) => {
              const seconds = Number(value);
              save(
                { health_refresh_seconds: seconds },
                `System health refreshes every ${String(seconds)} s`,
              );
            }}
            options={REFRESH_SECONDS.map((s) => ({ value: String(s), label: `${String(s)} s` }))}
          />
        </SettingRow>
      </Card>

      <SavedViewsCard />

      {busy && <p className="t-meta m-0">Saving…</p>}
      {error !== null && <ErrorNotice error={error} />}
    </div>
  );
}
