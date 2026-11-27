// The embedded native Coder UI registers its notification worker on the portal origin.
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const upstream = await fetch('http://coder:7080/serviceWorker.js', {
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(5000),
    });
    const type = upstream.headers.get('content-type') || '';
    if (upstream.ok && /^(?:text|application)\/javascript\b/i.test(type)) {
      return new Response(upstream.body, {
        headers: { 'Content-Type': type, 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' },
      });
    }
  } catch {
    // Let the browser retry registration when the native service is available.
  }
  return new Response(null, { status: 503, headers: { 'Cache-Control': 'no-store' } });
}
