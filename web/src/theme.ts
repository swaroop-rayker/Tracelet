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
  readonly border: string;
  readonly surface: string;
  readonly accent: string;
  readonly series: readonly string[];
  readonly sequential: readonly string[];
  readonly tiles: 'dark' | 'light';
}

function token(style: CSSStyleDeclaration, name: string): string {
  return style.getPropertyValue(name).trim();
}

/** The current theme's colours, read from the applied CSS. */
export function palette(): Palette {
  const style = getComputedStyle(document.documentElement);
  return {
    text: token(style, '--text'),
    muted: token(style, '--text-muted'),
    border: token(style, '--border'),
    surface: token(style, '--surface'),
    accent: token(style, '--accent'),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(style, `--chart-${String(i)}`)),
    sequential: [1, 2, 3, 4, 5].map((i) => token(style, `--seq-${String(i)}`)),
    tiles: token(style, '--tiles') === 'light' ? 'light' : 'dark',
  };
}
