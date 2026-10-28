import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['.tmp/**', 'dist/**', 'dist-public/**', 'public-build-input/**', 'public-build-audits/**', 'exports/**', 'fixtures/phase4a_generated/**', 'fixtures/phase5a_generated/**', 'public/reports/**', 'node_modules/**', 'test-results/**', 'playwright-report/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { rules: { '@typescript-eslint/no-explicit-any': 'off', '@typescript-eslint/no-unused-vars': 'off' } }
);
