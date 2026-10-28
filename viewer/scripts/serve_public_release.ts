import { createServer, type ServerResponse } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyPublicRelease } from './build_public_release.ts';

const mime: Record<string, string> = {
  '.css': 'text/css; charset=utf-8', '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.svg': 'image/svg+xml',
};

function send(response: ServerResponse, status: number, body: string) {
  response.writeHead(status, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' });
  response.end(body);
}

export async function startPublicReleaseServer(releaseRoot: string, port = 8000): Promise<{ url: string; close(): Promise<void> }> {
  const root = resolve(releaseRoot);
  const errors = verifyPublicRelease(root);
  if (errors.length) throw new Error(`Refusing to serve an invalid public release: ${errors.join('; ')}`);
  const siteRoot = join(root, 'site-package', 'site');
  const server = createServer((request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://127.0.0.1').pathname);
      const requested = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
      const path = resolve(siteRoot, ...requested.split('/'));
      const relation = relative(siteRoot, path);
      if (relation === '..' || relation.startsWith(`..${sep}`)) { send(response, 403, 'path escape rejected'); return; }
      if (!existsSync(path) || !statSync(path).isFile()) { send(response, 404, 'not found'); return; }
      response.writeHead(200, { 'content-type': mime[extname(path)] ?? 'application/octet-stream', 'cache-control': 'no-store' });
      response.end(readFileSync(path));
    } catch (error) { send(response, 500, error instanceof Error ? error.message : String(error)); }
  });
  await new Promise<void>((accept, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => { server.off('error', reject); accept(); });
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Local preview server did not expose a TCP port.');
  return {
    url: `http://127.0.0.1:${address.port}/`,
    close: () => new Promise<void>((accept, reject) => server.close((error) => error ? reject(error) : accept())),
  };
}

function option(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const releaseRoot = option('--release-root');
  const port = Number(option('--port') ?? 8000);
  if (!releaseRoot || !Number.isInteger(port) || port < 0 || port > 65535) {
    console.error('Usage: node --experimental-strip-types scripts/serve_public_release.ts --release-root <verified-release-directory> [--port 8000]');
    process.exitCode = 2;
  } else {
    startPublicReleaseServer(releaseRoot, port)
      .then(({ url }) => console.log(`Serving verified local-only release at ${url}. Press Ctrl+C to stop.`))
      .catch((error) => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
  }
}
