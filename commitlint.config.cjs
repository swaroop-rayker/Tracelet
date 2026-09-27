/**
 * Conventional commits (ES6).
 *
 * Scope is the F-ID where one applies, e.g. `feat(F4): add rDNS city-code
 * lexicon`. The scope list is deliberately permissive so a legitimate commit is
 * never blocked by a missing entry; the subject-case and length rules are the
 * ones that actually keep history readable.
 */
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'docs', 'refactor', 'test', 'chore', 'perf', 'build', 'ci', 'revert'],
    ],
    'subject-case': [2, 'never', ['pascal-case', 'upper-case']],
    'header-max-length': [2, 'always', 100],
    'body-max-line-length': [1, 'always', 100],
  },
};
