'use client';

export type DockerExport = {available: false} | {available: true; revision: string; size_bytes: number};
export type DownloadProject = {id: string; name: string};

export function useDockerExport(): DockerExport | undefined {
  return undefined;
}

export function DockerDownload(_props: {project: DownloadProject; bundle?: DockerExport}) {
  return null;
}
