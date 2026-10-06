/**
 * The three themes (F9.AC16): applying one, and reading its colours for the charts.
 *
 * The CSS custom properties in `index.css` are the single source of colour. ECharts and
 * Leaflet draw on a canvas and cannot read CSS, so they ask here, after the theme is
 * applied -- which is why every chart re-renders when the theme changes.
 */

export type ThemeName = 'semi_dark' | 'light' | 'dark';

export const THEMES: readonly { readonly name: ThemeName; readonly label: string }[] = [
  { name: 'semi_dark', label: 'Semi-dark' },
  { name: 'dark', label: 'Dark' },
  { name: 'light', label: 'Light' },
];

/** The API stores `semi_dark`; the stylesheet's attribute value is `semi-dark`. */
export function themeAttribute(theme: ThemeName): string {
  return theme === 'semi_dark' ? 'semi-dark' : theme;
}

export function asTheme(value: string): ThemeName {
  return value === 'light' || value === 'dark' ? value : 'semi_dark';
}

export function applyTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = themeAttribute(theme);
  window.dispatchEvent(new Event('tracelet:theme'));
}

export interface Palette {
  readonly text: string;
  readonly muted: string;
  readonly subtle: string;
  readonly border: string;
  readonly borderStrong: string;
  readonly surface: string;
  readonly surface2: string;
  readonly overlay: string;
  readonly accent: string;
  /** A selected area's fill on the geofence editor (DESIGN §16). */
  readonly accentBg: string;
  readonly ok: string;
  readonly warn: string;
  readonly error: string;
  /** Categorical series in order; the first is the accent, the last is neutral (DESIGN §6.3). */
  readonly series: readonly string[];
  readonly sequential: readonly string[];
  /** Land on the Geography map; the sea is the map's CSS background (ADR-0017). */
  readonly mapLand: string;
}

function token(style: CSSStyleDeclaration, name: string): string {
  return style.getPropertyValue(name).trim();
}

/** The number of categorical chart colours (`--chart-1` … `--chart-6`). */
export const SERIES_COUNT = 6;

/** The current theme's colours, read from the applied CSS. */
export function palette(): Palette {
  const style = getComputedStyle(document.documentElement);
  return {
    text: token(style, '--text'),
    muted: token(style, '--text-muted'),
    subtle: token(style, '--text-subtle'),
    border: token(style, '--border'),
    borderStrong: token(style, '--border-strong'),
    surface: token(style, '--surface'),
    surface2: token(style, '--surface-2'),
    overlay: token(style, '--overlay'),
    accent: token(style, '--accent'),
    accentBg: token(style, '--accent-bg'),
    ok: token(style, '--ok'),
    warn: token(style, '--warn'),
    error: token(style, '--error'),
    series: Array.from({ length: SERIES_COUNT }, (_, i) =>
      token(style, `--chart-${String(i + 1)}`),
    ),
    sequential: [1, 2, 3, 4, 5].map((i) => token(style, `--seq-${String(i)}`)),
    mapLand: token(style, '--map-land'),
  };
}
