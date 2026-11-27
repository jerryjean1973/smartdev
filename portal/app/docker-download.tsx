'use client';

import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';

export type DockerExport = { available: false } | { available: true; revision: string; size_bytes: number };
export type DownloadProject = { id: string; name: string };

const codingHost = 'coder.smartdev.youkandoit.com';
const managementOrigin = 'https://smartdev.youkandoit.com';

export function useDockerExport() {
  const [bundle, setBundle] = useState<DockerExport>();
  useEffect(() => {
    if (window.location.hostname === codingHost) return;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch('/api/admin/docker-export', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw Error();
        setBundle(await response.json());
      } catch {
        if (!controller.signal.aborted) setBundle({ available: false });
      }
    }
    void load();
    return () => controller.abort();
  }, []);
  return bundle;
}

export function DockerDownload({ project, bundle }: { project: DownloadProject; bundle?: DockerExport }) {
  // The coding portal delegates owner downloads to the management portal.
  const viaManagement = typeof window !== 'undefined' && window.location.hostname === codingHost;
  if (!viaManagement && !bundle?.available) return <small>{bundle ? 'Docker archive unavailable' : 'Checking Docker archive…'}</small>;
  return (
    <a href={(viaManagement ? managementOrigin : '') + '/api/admin/projects/' + project.id + '/smd-export'} download
      target={viaManagement ? '_blank' : undefined} rel={viaManagement ? 'noopener' : undefined}
      aria-label={'Download Docker for ' + project.name} title={bundle?.available ? 'SmartDev Docker Compose archive · ' + bundle.revision : 'Download the SmartDev Docker Compose archive from Command Center'}
      onClick={event => event.stopPropagation()}>
      <Download size={15} aria-hidden="true" /> Download Docker
    </a>
  );
}
