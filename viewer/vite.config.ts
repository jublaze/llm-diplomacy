import { defineConfig } from 'vite';

export default defineConfig(({ mode }) => {
  const publicBuild = mode === 'public';
  const base = publicBuild ? (process.env.PUBLIC_BASE_PATH ?? './') : '/';
  if (publicBuild && (/^[A-Za-z][A-Za-z\d+.-]*:/.test(base) || base.includes('..'))) {
    throw new Error('PUBLIC_BASE_PATH must be a deployable relative or root-relative URL path.');
  }
  return {
    base,
    publicDir: publicBuild ? (process.env.PUBLIC_BUILD_INPUT_ROOT ?? 'public-build-input') : 'public',
    esbuild: publicBuild ? { jsxDev: false } : undefined,
    build: {
      outDir: publicBuild ? (process.env.PUBLIC_BUILD_OUTPUT_ROOT ?? 'dist-public') : 'dist',
      sourcemap: false,
    },
    test: { environment: 'jsdom', globals: true, include: ['tests/**/*.test.{ts,tsx}'], exclude: ['tests/contract.test.ts', 'tests/portable-years.test.ts'] },
  };
});
