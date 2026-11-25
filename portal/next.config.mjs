const basePath = process.env.SMARTDEV_PREVIEW_BASE_PATH || '';
export default { basePath, skipTrailingSlashRedirect: true,
  env: { NEXT_PUBLIC_SMARTDEV_PREVIEW_PATH: basePath } };
