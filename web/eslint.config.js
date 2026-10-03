import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default tseslint.config(
  { ignores: ['dist', 'src/api/generated'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // ES1: no `any`, and no silent unsafe access through one.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unsafe-assignment': 'error',
      '@typescript-eslint/no-unsafe-member-access': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
      // DESIGN UI-5: icons come from the registry, so a glyph is swapped in one place and
      // only named icons are bundled.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'lucide-react',
              message: 'Import icons from @/components/icons (DESIGN §4.8).',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/components/icons.ts'],
    rules: { 'no-restricted-imports': 'off' },
  },
  {
    // The generated OpenAPI client is never hand-edited (F14.AC9), so linting it
    // would only ever produce noise nobody is allowed to fix.
    files: ['src/api/generated/**'],
    rules: {},
  },
);
