/**
 * WCAG AA contrast for all three themes (F9.AC16, NFR7.AC1), read from the stylesheet
 * itself so a token edit cannot drift past it.
 *
 * Text pairs need 4.5:1. Chart marks and the sequential ramp's darkest-to-lightest end
 * need 3:1 against the panel surface (WCAG 1.4.11, non-text contrast). The lowest
 * sequential step is exempt by design: it means "few", and every chart that uses the
 * ramp also states its values as text (NFR7.AC3).
 */

import { readFileSync } from 'node:fs';
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

describe.each(Object.entries(THEMES))('theme %s', (_, tokens) => {
  it.each(['--text', '--text-muted', '--accent', '--ok', '--warn', '--error'])(
    '%s is readable on the background and on panels (4.5:1)',
    (name) => {
      for (const ground of ['--bg', '--surface', '--surface-2']) {
        expect(contrast(get(tokens, name), get(tokens, ground))).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  it.each([1, 2, 3, 4, 5, 6, 7, 8])('chart colour %i is visible on a panel (3:1)', (i) => {
    expect(
      contrast(get(tokens, `--chart-${String(i)}`), get(tokens, '--surface')),
    ).toBeGreaterThanOrEqual(3);
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

  it('the sequential ramp spans a visible range', () => {
    expect(contrast(get(tokens, '--seq-1'), get(tokens, '--seq-5'))).toBeGreaterThanOrEqual(3);
    expect(contrast(get(tokens, '--seq-5'), get(tokens, '--surface'))).toBeGreaterThanOrEqual(3);
  });
});
