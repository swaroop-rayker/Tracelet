/**
 * Ground-truth figures (DESIGN §16 M8, RISKS R9): a share is never shown without its sample,
 * and "right" is decided as the server decides it (accuracy/replay.py `right`).
 */

import { describe, expect, it } from 'vitest';
import type { TargetCheck } from '@/api/groundtruth';
import {
  interval,
  isRight,
  normalisePlace,
  sample,
  share,
  targetText,
} from '@/components/groundtruth/figures';

function target(over: Partial<TargetCheck>): TargetCheck {
  return {
    id: 'admin1.strict_precision',
    population: 'network_only',
    level: 'admin1',
    metric: 'strict_precision',
    target: 0.99,
    value: 1,
    n: 31,
    gated: true,
    status: 'met',
    ...over,
  };
}

describe('figures', () => {
  it('shows the sample and the interval beside the share', () => {
    const p = { k: 31, n: 31, value: 1, ci95: [0.8897, 1] as [number, number] };
    expect(share(p)).toBe('100%');
    expect(sample(p)).toBe('31 of 31');
    expect(interval(p.ci95)).toBe('95 %: 89%–100%');
  });

  it('says nothing was measured rather than showing zero', () => {
    expect(share({ k: 0, n: 0, value: null, ci95: null })).toBe('—');
    expect(interval(null)).toBe('no interval: nothing measured');
  });

  it('spells the verdict out in words', () => {
    expect(targetText(target({}))).toBe('≥ 99% · target met ✓');
    expect(targetText(target({ status: 'missed', value: 0.9 }))).toBe('≥ 99% · target missed ✗');
    expect(targetText(target({ target: 0.995, status: 'unmeasured', value: null }))).toBe(
      '≥ 99.5% · not measured yet',
    );
    expect(targetText(target({ target: null, gated: false, status: 'reported' }))).toBe(
      'Reported; no target yet',
    );
  });
});

describe('isRight', () => {
  const truth = { country_code: 'IN', admin1: 'Maharashtra', admin2: null, city: 'Aurangabad' };

  it('needs every shallower level the truth names to agree', () => {
    const wrongState = { country_code: 'IN', admin1: 'Bihar', admin2: null, city: 'Aurangabad' };
    expect(isRight(truth, wrongState, 'city')).toBe(false);
    expect(isRight(truth, { ...wrongState, admin1: 'maharashtra' }, 'city')).toBe(true);
  });

  it('compares names as the engine votes on them', () => {
    expect(normalisePlace(' Tamil-Nadu ')).toBe('tamil nadu');
    expect(isRight(truth, { country_code: 'in', admin1: 'MAHARASHTRA' }, 'admin1')).toBe(true);
  });

  it('is never right where nothing was stated', () => {
    expect(isRight(truth, { country_code: 'IN', admin1: null }, 'admin1')).toBe(false);
  });
});
