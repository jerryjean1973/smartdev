import { NextRequest, NextResponse } from 'next/server';

const origin = 'https://smartdev.youkandoit.com';
const coderOrigin = 'https://coder.smartdev.youkandoit.com';
const project = 'bca501cf-7f58-4cd6-b254-33bf4fe48c7c';
const previewPath = '/@validation/p-xssqdt37lbgnnmsugo7u7zempq/apps/dev-preview';
const cookieName = 'smartdev_dev_portal';

async function proxy(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  if (process.env.NEXT_PUBLIC_SMARTDEV_PREVIEW_PATH !== previewPath)
    return new NextResponse(null, {status: 404});
  const path = (await context.params).path.join('/');
  const read = request.method === 'GET' &&
    ['projects', 'task-navigation', 'projects/' + project + '/objectives'].includes(path);
  const session = request.method === 'POST' && ['login', 'logout'].includes(path);
  if (!read && !session) return new NextResponse(null, {status: 404});
  if (session && request.headers.get('origin') !== coderOrigin)
    return new NextResponse(null, {status: 403});

  const headers = new Headers();
  const token = request.cookies.get(cookieName)?.value;
  if (token && /^[a-zA-Z0-9._-]+$/.test(token)) headers.set('Cookie', '__Host-command=' + token);
  if (session) {
    headers.set('Origin', origin);
    headers.set('Content-Type', 'application/json');
  }
  let upstream;
  try {
    upstream = await fetch(origin + '/api/' + path, {
      method: request.method, headers, cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(10000),
      ...(path === 'login' ? {body: await request.text()} : {}),
    });
  } catch {
    return NextResponse.json({detail: 'SmartDev API is unavailable.'}, {status: 502});
  }

  const data = await upstream.json().catch(() => null);
  const result = NextResponse.json(
    upstream.ok && Array.isArray(data) && path !== 'projects/' + project + '/objectives'
      ? data.filter(row => (path === 'projects' ? row.id : row.project_id) === project)
      : data,
    {status: upstream.status, headers: {'Cache-Control': 'no-store'}},
  );
  if (upstream.ok && session) {
    const value = upstream.headers.get('set-cookie')?.match(/^__Host-command=([a-zA-Z0-9._-]+);/)?.[1];
    if (path === 'login' && !value)
      return NextResponse.json({detail: 'SmartDev did not establish a session.'}, {status: 502});
    result.cookies.set(cookieName, value || '', {
      secure: true, httpOnly: true, sameSite: 'strict', path: previewPath + '/', maxAge: value ? 28800 : 0,
    });
  }
  return result;
}

export { proxy as GET, proxy as POST };
