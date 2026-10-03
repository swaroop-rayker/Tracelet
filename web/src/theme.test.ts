/**
 * WCAG AA contrast for all three themes (F9.AC16, NFR7.AC1), read from the stylesheet
 * itself so a token edit cannot drift past it.
 *
 * Text pairs need 4.5:1. Chart marks and the sequential ramp's darkest-to-lightest end
 * need 3:1 against the panel surface (WCAG 1.4.11, non-text contrast). The lowest
 * sequential step is exempt by design: it means "few", and every chart that uses the
 * ramp also states its values as text (NFR7.AC3).
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8');

function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`no ${selector} block`);
  const open = css.indexOf('{', start);
  const close = css.indexOf('}', open);
  const tokens: Record<string, string> = {};
  for (const match of css.slice(open + 1, close).matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})/gi)) {
    const [, name, value] = match;
    if (name !== undefined && value !== undefined) tokens[name] = value;
  }
  return tokens;
}

function luminance(hex: string): number {
  const channel = (offset: number): number => {
    const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = {
  'semi-dark': block(":root[data-theme='semi-dark']"),
  dark: block(":root[data-theme='dark']"),
  light: block(":root[data-theme='light']"),
};

function get(tokens: Record<string, string>, name: string): string {
  const value = tokens[name];
  if (value === undefined) throw new Error(`missing ${name}`);
  return value;
}

const GROUNDS = ['--bg', '--sidebar', '--surface', '--surface-2', '--overlay'];

describe.each(Object.entries(THEMES))('theme %s', (_, tokens) => {
  it.each(['--text', '--text-muted', '--text-subtle', '--accent', '--ok', '--warn', '--error'])(
    '%s is readable on every surface layer (4.5:1)',
    (name) => {
      for (const ground of GROUNDS) {
        expect(contrast(get(tokens, name), get(tokens, ground))).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  // Badges and alerts put status text on its own tint (DESIGN §5.4, §5.6).
  it.each([
    ['--ok', '--ok-bg'],
    ['--warn', '--warn-bg'],
    ['--error', '--error-bg'],
    ['--accent', '--accent-bg'],
    ['--text', '--accent-bg'],
  ])('%s is readable on %s (4.5:1)', (fg, bg) => {
    expect(contrast(get(tokens, fg), get(tokens, bg))).toBeGreaterThanOrEqual(4.5);
  });

  it('text on a filled accent button is readable (4.5:1)', () => {
    expect(contrast(get(tokens, '--on-accent'), get(tokens, '--accent'))).toBeGreaterThanOrEqual(
      4.5,
    );
  });

  it.each([1, 2, 3, 4, 5, 6])('chart colour %i is visible on a panel (3:1)', (i) => {
    expect(
      contrast(get(tokens, `--chart-${String(i)}`), get(tokens, '--surface')),
    ).toBeGreaterThanOrEqual(3);
  });

  it('the primary series is the accent (DESIGN §4.1)', () => {
    expect(get(tokens, '--chart-1')).toBe(get(tokens, '--accent'));
  });

  // The Geography map (ADR-0017): the fewest visits must not look like no visits, and
  // land must read as land. Visual distinction, not text, so the bar is lower than 3:1;
  // every shaded area also carries its count as text (NFR7.AC3).
  it('the map keeps land, sea and the lowest shade apart', () => {
    expect(contrast(get(tokens, '--map-land'), get(tokens, '--map-sea'))).toBeGreaterThanOrEqual(
      1.3,
    );
    expect(contrast(get(tokens, '--seq-1'), get(tokens, '--map-land'))).toBeGreaterThanOrEqual(1.6);
  });

  it('the sequential ramp spans a visible range, in order', () => {
    expect(contrast(get(tokens, '--seq-1'), get(tokens, '--seq-5'))).toBeGreaterThanOrEqual(3);
    expect(contrast(get(tokens, '--seq-5'), get(tokens, '--surface'))).toBeGreaterThanOrEqual(3);
    const ramp = [1, 2, 3, 4, 5].map((i) => luminance(get(tokens, `--seq-${String(i)}`)));
    const rising = ramp.every((v, i) => i === 0 || v > (ramp[i - 1] ?? 0));
    const falling = ramp.every((v, i) => i === 0 || v < (ramp[i - 1] ?? 1));
    expect(rising || falling).toBe(true);
  });
});

// DESIGN UI-2: colour lives in the theme blocks only. Outside them, the stylesheet and the
// TypeScript sources carry no raw hex colours.
describe('no raw colours outside the tokens', () => {
  it('the stylesheet has hex colours only inside the three theme blocks', () => {
    const themeBlocks = [
      ":root[data-theme='semi-dark']",
      ":root[data-theme='dark']",
      ":root[data-theme='light']",
    ];
    let rest = css;
    for (const selector of themeBlocks) {
      const start = rest.indexOf(selector);
      const close = rest.indexOf('}', rest.indexOf('{', start));
      rest = rest.slice(0, start) + rest.slice(close + 1);
    }
    expect(rest.match(/#[0-9a-f]{3,8}\b/gi) ?? []).toEqual([]);
  });

  it.each(['primitives.css', 'shell.css', 'data.css'])(
    'styles/%s has no hex colours at all',
    (file) => {
      const sheet = readFileSync(new URL(`./styles/${file}`, import.meta.url), 'utf8');
      expect(sheet.match(/#[0-9a-f]{3,8}\b/gi) ?? []).toEqual([]);
    },
  );

  it('no TypeScript source carries a hex colour', () => {
    const root = fileURLToPath(new URL('.', import.meta.url));
    const files = (readdirSync(root, { recursive: true }) as string[]).filter(
      (f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f) && !f.includes('generated'),
    );
    expect(files.length, 'the scan must actually see the sources').toBeGreaterThan(20);
    const offenders = files.filter((f) =>
      /['"`]#[0-9a-f]{3,8}['"`]/i.test(readFileSync(join(root, f), 'utf8')),
    );
    expect(offenders).toEqual([]);
  });
});
