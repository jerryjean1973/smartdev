'use client';

import {useEffect, useState} from 'react';
import DeploymentLogin from './deployment-login';

type Connections = {
  ipv4: string;
  ssh_user: 'root' | 'ubuntu';
  portal_url: string;
  coder_url: string;
  apps: {project_id: string; revision: string; url: string; runtime_status?: 'awaiting_tenant_configuration' | 'runtime_unverified'}[];
};

export default function SmdConnections({taskId, projectNames = {}}: {
  taskId: string;
  projectNames?: Record<string, string>;
}) {
  const [connections, setConnections] = useState<Connections>();
  const [active, setActive] = useState(false);
  const [notice, setNotice] = useState('Loading verified connection details…');

  useEffect(() => {
    const controller = new AbortController();
    setConnections(undefined);
    setActive(false);
    setNotice('Loading verified connection details…');
    async function load() {
      try {
        const response = await fetch('/api/admin/smd-deploy-tasks/' + taskId + '/receipt', {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw Error();
        const receipt: {verified: boolean; active: boolean; connections: Connections | null} = await response.json();
        if (controller.signal.aborted) return;
        if (receipt.verified && receipt.connections) {
          setConnections(receipt.connections);
          setActive(receipt.active);
        } else {
          setNotice('No verified connection details were recorded for this deployment.');
        }
      } catch {
        if (!controller.signal.aborted) setNotice('Could not load deployment connection details.');
      }
    }
    void load();
    return () => controller.abort();
  }, [taskId]);

  if (!connections) return <p role="status">{notice}</p>;
  return <section aria-label="Verified connection details">
    <h4>Connection details</h4>
    <dl>
      <dt>VM hostname</dt><dd>{new URL(connections.portal_url).hostname}</dd>
      <dt>Public IP</dt><dd><code>{connections.ipv4}</code></dd>
      <dt>SSH</dt><dd><code>ssh {connections.ssh_user}@{connections.ipv4}</code></dd>
      <dt>SmartDev</dt><dd>{active ? <DeploymentLogin taskId={taskId} portal={connections.portal_url} /> : 'No active portal'}</dd>
      <dt>Coder</dt><dd><a href={connections.coder_url}>Open Coder</a></dd>
    </dl>
    {connections.apps.length > 0 && <>
      <h4>Deployed apps</h4>
      <ul>{connections.apps.map(app => <li key={app.project_id}>
        <a href={app.url}>{projectNames[app.project_id] ?? new URL(app.url).hostname}</a>{' '}
        <code>{app.revision}</code>
        {app.runtime_status === 'awaiting_tenant_configuration' && <> · Installed, awaiting tenant configuration</>}
        {app.runtime_status === 'runtime_unverified' && <> · Installed, application health unverified</>}
      </li>)}</ul>
    </>}
  </section>;
}
