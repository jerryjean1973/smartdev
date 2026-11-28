'use client';

// SMD fleet deployment is a Command Center capability, never a tenant capability.
export default function SmdDeployment(_props: {
  projectId: string;
  projectName: string;
  compact?: boolean;
  actionContainer?: HTMLElement | null;
  versionContainer?: HTMLElement | null;
  blocker?: string;
}) {
  return null;
}
