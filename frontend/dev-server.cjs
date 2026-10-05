// Serve the frontend and forward its existing relative API requests.
// Run: node frontend/dev-server.cjs (backend defaults to localhost:8000).
const http = require('node:http');
const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');

const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

function createFrontendServer({ apiOrigin = 'http://127.0.0.1:8000' } = {}) {
  const origin = new URL(apiOrigin);
  if (!['http:', 'https:'].includes(origin.protocol)) {
    throw new Error('API_ORIGIN must be an HTTP or HTTPS origin.');
  }
  const transport = origin.protocol === 'https:' ? https : http;
  return http.createServer((request, response) => {
    if (/^\/(?:auth|concerts|orders|health)(?:\/|\?|$)/.test(request.url)) {
      const upstream = transport.request(new URL(request.url, origin), {
        method: request.method,
        headers: { ...request.headers, host: origin.host },
      }, (backendResponse) => {
        response.writeHead(backendResponse.statusCode, backendResponse.headers);
        backendResponse.pipe(response);
      });
      upstream.setTimeout(15000, () => upstream.destroy(new Error('API timeout')));
      upstream.on('error', () => {
        if (!response.headersSent) {
          response.writeHead(502, { 'Content-Type': 'application/json' });
          response.end(JSON.stringify({ error: 'BackendUnavailable' }));
        } else response.destroy();
      });
      request.on('aborted', () => upstream.destroy());
      response.on('close', () => { if (!response.writableEnded) upstream.destroy(); });
      request.pipe(upstream);
      return;
    }
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' });
      response.end();
      return;
    }
    let pathname;
    try { pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname); }
    catch { response.writeHead(400); response.end(); return; }
    const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
    const target = path.resolve(__dirname, relative);
    const type = mimeTypes[path.extname(target)];
    if (!target.startsWith(__dirname + path.sep) || !type || relative.split(/[\\/]/).some((part) => part.startsWith('.'))) {
      response.writeHead(404); response.end(); return;
    }
    fs.stat(target, (error, stat) => {
      if (error || !stat.isFile()) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, {
        'Content-Type': type,
        'Content-Length': stat.size,
        'Cache-Control': 'no-cache',
        'X-Content-Type-Options': 'nosniff',
      });
      if (request.method === 'HEAD') { response.end(); return; }
      fs.createReadStream(target).on('error', () => response.destroy()).pipe(response);
    });
  });
}

if (require.main === module) {
  const port = Number(process.env.FRONTEND_PORT || 3000);
  const apiOrigin = process.env.API_ORIGIN || 'http://127.0.0.1:8000';
  createFrontendServer({ apiOrigin }).listen(port, '127.0.0.1', () => {
    console.log(`HIT THE VIBE: http://localhost:${port}`);
  });
}

module.exports = { createFrontendServer };
