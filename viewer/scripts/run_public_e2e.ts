import { spawn } from 'node:child_process';
import { resolve } from 'node:path';

const repositoryRoot = resolve(import.meta.dirname, '..');
const port = 4175;
const url = `http://127.0.0.1:${port}/`;

async function waitForServer() {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch { /* preview is still starting */ }
    await new Promise((accept) => setTimeout(accept, 100));
  }
  throw new Error(`Public preview did not become ready at ${url}.`);
}

const preview = spawn(process.execPath, [
  resolve(repositoryRoot, 'node_modules', 'vite', 'bin', 'vite.js'),
  'preview', '--outDir', 'dist-public', '--host', '127.0.0.1', '--port', String(port),
], { cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe'] });

try {
  await waitForServer();
  const tests = spawn(process.execPath, [
    resolve(repositoryRoot, 'node_modules', '@playwright', 'test', 'cli.js'),
    'test', '--config', 'playwright.public.config.ts', '--reporter=line',
  ], { cwd: repositoryRoot, stdio: 'inherit' });
  const code = await new Promise<number>((accept, reject) => {
    tests.once('error', reject);
    tests.once('exit', (value) => accept(value ?? 1));
  });
  if (code !== 0) process.exitCode = code;
} finally {
  preview.kill('SIGTERM');
  await Promise.race([
    new Promise<void>((accept) => preview.once('exit', () => accept())),
    new Promise<void>((accept) => setTimeout(accept, 2_000)),
  ]);
}
