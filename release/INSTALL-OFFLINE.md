# Import SmartDev on a Docker host

Use an amd64 Docker host with the Docker Compose plugin and Python 3.12 or later.
Check the archive's SHA-256 against the value included in the download response before extracting.

1. Pick a UUID for this installation and run the following as an authorized administrator. Replace the archive path and UUID.

   ```sh
   project=00000000-0000-4000-8000-000000000001
   archive=/path/to/smartdev-stack.tar
   install -d -m 0700 "/opt/smartdev/$project/releases/local"
   cd "/opt/smartdev/$project/releases/local"
   tar -xf "$archive"
   docker image load --input images.tar.gz
   bash configure.sh "$project"
   docker compose --project-name "smd-$project" up -d --wait --pull never --no-build
   ```

2. Open the SmartDev HTTPS address shown in the generated Caddy configuration. The new deployment has a generated owner password under `/opt/smartdev/PROJECT_UUID/data/owner-password`. Trust this host's Caddy root certificate for direct IP access, or configure a hostname and DNS before running the stack.
3. This archive contains the platform images, configuration, model, and workspace image. Import the Git repositories you are authorized to use into the new environment.

This is a Docker Compose bundle. Deploying it on Kubernetes requires Kubernetes resources for the workloads, services, storage, secrets, and ingress.
