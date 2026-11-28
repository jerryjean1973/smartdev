'use client';

import type {ReactNode} from 'react';

type Project = {
  id: string;
  name: string;
  repository: string;
  workspace_url: string | null;
  archived: boolean;
  status: string;
};

// Tenant SMDs have no Command Center tenant/deployment administration surface.
export default function AdminProjects<T extends Project>(_props: {
  projects: T[];
  dockerExport?: unknown;
  activeTab?: 'deployments' | 'tenants';
  signOut?: ReactNode;
  onAsk?: () => void;
}) {
  return null;
}
