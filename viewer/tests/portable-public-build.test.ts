// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stagePublicReports, verifyPublicBuild } from '../scripts/public_build.ts';
import { syntheticReport } from './synthetic-report.ts';
import { writePublicDemo } from '../scripts/build_public_demo.ts';
import { npmBuildInvocation, withTemporaryRoot } from './public-build-test-utils.ts';

const viewerRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
function approvedReport() {
  const report = syntheticReport(1901, 1901) as any;
  report.source.visibility_mode = 'public';
  report.extensions.audience = {
    schema_version: 'diplomacy_report_audience_v1', policy_version: 'diplomacy_audience_policy_v1', mode: 'postgame_public',
    reader_description: 'Synthetic approved public test report.', approved_for_static_public_distribution: true,
    projection: { generator: 'portable-test', source_bundle_id: 'synthetic-source', source_bundle_sha256: 'a'.repeat(64) },
  };
  const source = { source_id: 'order-source', artifact_path: 'evidence/orders.json', sha256: 'b'.repeat(64), visibility: 'public', kind: 'orders' };
  report.evidence_index['order-source'] = source;
  report.orders.order_1 = { order_id: 'order_1', phase_id: '1901_spring_movement', power: 'France', notation: 'A par H', effective_status: 'succeeds', source_ref: 'order-source', payload: {} };
  report.years[0].phases[0].orders = ['order_1'];
  return report;
}

function writeReport(root: string, report: unknown) {
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, 'report.json'), JSON.stringify(report));
}

describe('portable public build', () => {
  it('stages the authored synthetic demo without private game artifacts', async () => {
    await withTemporaryRoot('vci-demo-', async (root) => {
      const source = join(root, 'demo');
      writePublicDemo(source);
      expect(() => writePublicDemo(source)).toThrow(/already exists/);
      stagePublicReports({ sourceRoot: source, outputRoot: join(root, 'stage') });
      const audit = await verifyPublicBuild({ distRoot: join(root, 'stage') });
      expect(audit.errors).toEqual([]);
      expect(audit.bundle_count).toBe(1);
      expect(audit.private_record_count).toBe(0);
      expect(audit.omniscient_evidence_count).toBe(0);
    });
  });
  it('builds a reviewed zero-message public report and rejects a private variant', async () => {
    await withTemporaryRoot('vci-public-smoke-', async (root) => {
    const source = join(root, 'source');
    const stage = join(root, 'stage');
    const dist = join(root, 'dist');
    writeReport(source, approvedReport());
    const staged = stagePublicReports({ sourceRoot: source, outputRoot: stage });
    expect(staged.error_count).toBe(0);
    const invocation = npmBuildInvocation(process.platform, process.env.ComSpec);
    const child = spawnSync(invocation.command, invocation.args, {
      cwd: viewerRoot, encoding: 'utf8', timeout: 120_000,
      env: { ...process.env, PUBLIC_BUILD_INPUT_ROOT: stage, PUBLIC_BUILD_OUTPUT_ROOT: dist },
    });
    expect(child.status, `${child.error ?? ''}\n${child.stdout ?? ''}\n${child.stderr ?? ''}`).toBe(0);
    const audit = await verifyPublicBuild({ distRoot: dist });
    expect(audit.errors).toEqual([]);
    expect(audit.bundle_count).toBe(1);
    expect(audit.message_count).toBe(0);
    expect(audit.released_message_count).toBe(0);

    const privateSource = join(root, 'private-source');
    const privateReport = approvedReport();
    privateReport.private_records.secret = { agent_id: 'agent', source_ref: 'private-source', payload: {} };
    privateReport.evidence_index['private-source'] = { source_id: 'private-source', artifact_path: 'private.json', sha256: 'c'.repeat(64), visibility: 'private', kind: 'private_record' };
    writeReport(privateSource, privateReport);
    expect(() => stagePublicReports({ sourceRoot: privateSource, outputRoot: join(root, 'private-stage') })).toThrow(/public projection cannot contain private records|public-safe/i);
    });
  }, 120_000);

  it('keeps invalid staging as a hard failure before Vite emits a public build', async () => {
    await withTemporaryRoot('vci-public-invalid-', async (root) => {
    const emptyStage = join(root, 'empty-stage');
    const dist = join(root, 'dist');
    mkdirSync(emptyStage);
    const invocation = npmBuildInvocation(process.platform, process.env.ComSpec);
    const child = spawnSync(invocation.command, invocation.args, {
      cwd: viewerRoot, encoding: 'utf8', timeout: 60_000,
      env: { ...process.env, PUBLIC_BUILD_INPUT_ROOT: emptyStage, PUBLIC_BUILD_OUTPUT_ROOT: dist },
    });
    expect(child.status).not.toBe(0);
    expect(`${child.error ?? ''}\n${child.stdout ?? ''}\n${child.stderr ?? ''}`).toContain('Public stage verification failed');
    expect(() => readFileSync(join(dist, 'index.html'))).toThrow();
    });
  });

  it('builds a portable npm invocation and removes its temp root after failures', async () => {
    expect(npmBuildInvocation('linux')).toEqual({ command: 'npm', args: ['run', 'build:public'] });
    expect(npmBuildInvocation('win32', 'C:\\Windows\\System32\\cmd.exe')).toEqual({
      command: 'C:\\Windows\\System32\\cmd.exe', args: ['/d', '/s', '/c', 'npm run build:public'],
    });
    let createdRoot = '';
    await expect(withTemporaryRoot('vci-public-cleanup-', async (root) => {
      createdRoot = root;
      throw new Error('synthetic failure');
    })).rejects.toThrow('synthetic failure');
    expect(createdRoot).not.toBe('');
    expect(existsSync(createdRoot)).toBe(false);
  });
});
