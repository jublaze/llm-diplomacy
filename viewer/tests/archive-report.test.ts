// @vitest-environment node
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
const run = (...args: string[]) => spawnSync('python', ['scripts/archive_report.py', ...args], { cwd: process.cwd(), encoding: 'utf8' });

describe('Phase 4D deterministic archive', () => {
  it('creates byte-identical ZIPs and verifies a clean extraction', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-archive-'));
    const packageRoot = join(root, 'package');
    mkdirSync(join(packageRoot, 'site'), { recursive: true });
    writeFileSync(join(packageRoot, 'README.md'), 'Serve site over HTTP.\n');
    writeFileSync(join(packageRoot, 'site', 'index.html'), '<h1>Portable viewer</h1>\n');
    const files = ['README.md', 'site/index.html'].map((path) => {
      const bytes = readFileSync(join(packageRoot, ...path.split('/')));
      return { path, bytes: bytes.length, sha256: sha(bytes) };
    });
    writeFileSync(join(packageRoot, 'package-manifest.json'), JSON.stringify({ schema_version: 'diplomacy_archive_manifest_v1', package_id: 'test', audience: 'omniscient_postgame', report_ids: [], files }, null, 2));
    const first = join(root, 'first.zip');
    const second = join(root, 'second.zip');

    expect(run('create', '--package-root', packageRoot, '--output', first).status).toBe(0);
    expect(run('create', '--package-root', packageRoot, '--output', second).status).toBe(0);
    expect(readFileSync(first)).toEqual(readFileSync(second));
    const verified = run('verify', '--archive', first, '--extract-to', join(root, 'extracted'));
    expect(verified.status, verified.stderr).toBe(0);
  });

  it('rejects a checksum-tampered archive', () => {
    const root = mkdtempSync(join(tmpdir(), 'viewer-archive-tamper-'));
    const packageRoot = join(root, 'package');
    mkdirSync(join(packageRoot, 'site'), { recursive: true });
    const original = 'original\n';
    writeFileSync(join(packageRoot, 'site', 'report.json'), original);
    writeFileSync(join(packageRoot, 'package-manifest.json'), JSON.stringify({ schema_version: 'diplomacy_archive_manifest_v1', package_id: 'tamper', audience: 'omniscient_postgame', report_ids: [], files: [{ path: 'site/report.json', bytes: Buffer.byteLength(original), sha256: sha(original) }] }, null, 2));
    writeFileSync(join(packageRoot, 'site', 'report.json'), 'tampered\n');
    const archive = join(root, 'tampered.zip');
    expect(run('create', '--package-root', packageRoot, '--output', archive, '--skip-input-verification').status).toBe(0);
    const verified = run('verify', '--archive', archive, '--extract-to', join(root, 'extracted'));
    expect(verified.status).not.toBe(0);
    expect(`${verified.stdout}\n${verified.stderr}`).toMatch(/checksum mismatch/i);
  });
});
