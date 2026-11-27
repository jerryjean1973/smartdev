'use client';

import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';

export default function DeploymentLogin({ taskId, portal }: { taskId: string; portal: string }) {
  const url = '/api/admin/compute/deployments/' + taskId + '/portal-login';
  const [result, setResult] = useState({ url: '', ready: false, label: '' });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      let ready = false;
      let label = 'Portal unreachable';
      try {
        const response = await fetch(url, { cache: 'no-store', signal: controller.signal });
        if (response.ok && (await response.json()).ready === true) {
          ready = true;
          label = 'Open SmartDev Portal';
        } else if (response.status === 409) {
          label = 'Portal sign-in setup required';
        } else if (response.status === 401) {
          label = 'Sign in to Command Center';
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      if (!controller.signal.aborted) {
        setResult({ url, ready, label });
        if (!ready) timer = setTimeout(check, 30000);
      }
    }
    void check();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [url]);
  if (result.url !== url) return <small role="status">Checking portal sign-in…</small>;
  if (!result.ready) return (
    <div>
      <small role="status">{result.label}</small>
      <a href={portal} target="_blank" rel="noopener noreferrer">Open portal sign-in</a>
    </div>
  );
  return (
    <form method="post" action={url} target="_blank" rel="noopener">
      <button type="submit">Open SmartDev Portal <ExternalLink size={13} aria-hidden="true" /></button>
    </form>
  );
}
