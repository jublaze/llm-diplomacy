import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export function npmBuildInvocation(platform: NodeJS.Platform, comspec?: string) {
  return platform === 'win32'
    ? { command: comspec ?? 'cmd.exe', args: ['/d', '/s', '/c', 'npm run build:public'] }
    : { command: 'npm', args: ['run', 'build:public'] };
}

export async function withTemporaryRoot<T>(prefix: string, run: (root: string) => T | Promise<T>): Promise<T> {
  const root = mkdtempSync(join(tmpdir(), prefix));
  try {
    return await run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
