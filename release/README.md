# Standalone SmartDev release

Build a self-contained release on an amd64 Docker host with the reviewed local images and the local Qwen 2.5 1.5B model store.

```sh
python3 scripts/build-smd-release.py REVISION /path/to/release.tar \
  --offline --model-store /path/to/ollama/models
python3 scripts/build-smd-installer.py HTTPS_BUNDLE_URL BUNDLE_SHA256 REVISION /path/to/install.sh
```

The release builder reports the bundle hash. The installer builder reports the bootstrap hash. Pin both to the reviewed revision in the environment's deployment offer. Publish the two artifacts using an approved release location. Allow enough destination disk space for Docker images, the archive, staged extraction, and new project data.

The offline bundle includes API, portal, PostgreSQL, Redis, OpenBao, Coder, HTTPS ingress, and the mandatory local Architect classification service. `--model-store` is required and must provide the pinned `qwen2.5:1.5b` model; release construction fails closed when it is absent. A registry-backed release accepts a JSON mapping of digest-pinned images using `--images`. Names must match the keys in `scripts/build-smd-release.py`.

The installer assigns a stable project directory at `/opt/smartdev/PROJECT_UUID`. The project data directory holds the generated owner password, session material, and OpenBao recovery file. Use an authorized host session to retrieve the owner password. Keep project data and the Docker volumes under the owner's backup and recovery procedures. The OpenBao recovery file enables automatic unsealing of this single-host Vault and requires host-root protection.

Without a configured `data/public-host`, the portal uses the host's source IPv4 address and a private Caddy CA. Add the Caddy root certificate to the owner's trust store before opening the portal. For a public certificate, set `data/public-host` to a DNS name already routed to this host and allow inbound HTTP and HTTPS. Set `data/private-tls` when an explicitly configured hostname should use the private CA.

Grouped Command Center deployments pin one Git commit per selected project. The runner assigns separate portal, Coder, and app hostnames on HTTPS port 443. The installer seeds each selected workspace from a read-only Git bundle and verifies the pinned checkout, Coder agent connection, and VS Code and Codex Chat endpoints before reporting success.

Each selected application needs a committed `Dockerfile` and either one exposed TCP port or a committed `.smartdev/deploy.json` containing `{"port":8080}`. The installer builds from the pinned Git tree, starts one container per app, and routes it through Caddy on a project-specific internal Docker network. The app must return HTTP 2xx at its public root for the runner to report a verified deployment. GitHub credentials are scoped to the selected repository and used only during checkout.

For a Command Center rollout, use `scripts/stage-smd-release.py` to validate the fresh archive and write the private release environment file. The API needs the release overlay and a GitHub App installation authorized for the selected repositories. The runner needs the authorized internal DNS zone, an RFC 2136 update server and scoped key, and an existing owner Vultr SSH key. The VM must be able to resolve the internal names, and clients must trust the certificate authority used for those names. The console shows verified connection details after receiving the signed health receipt.

The internal Architect endpoint provides preliminary classification using the bundled Qwen model. Architecture sign-off, Codex credentials, external MIG authorization, full BRIPe, customer data, and existing workspace files require their own enrollment or migration workflow.
