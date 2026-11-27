FROM node:22-bookworm-slim
ARG CODE_SERVER_VERSION=4.104.2
ARG CODEX_VERSION=0.171.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget chromium chromium-sandbox fonts-liberation openssh-client git git-lfs python3 python3-venv bubblewrap tini \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && curl -fsSL https://code-server.dev/install.sh | sh -s -- --method standalone --version "${CODE_SERVER_VERSION}" --prefix /usr/local \
    && mkdir -p /projects /home/node && chown node:node /projects /home/node
COPY --chown=node:node codex-browser /opt/command-extensions/command-center.codex-browser
COPY --chown=node:node chat /opt/command-chat
COPY --chown=node:node preview /opt/command-preview
COPY --chown=node:node codex-browser/chat.js /opt/command-chat/chat.js
COPY requirements.toml /etc/codex/requirements.toml
COPY git-mcp.toml /etc/codex/config.toml
COPY workspace_git.py /usr/local/bin/workspace_git.py
COPY command-dev-login.cjs /usr/local/lib/command-dev-login.cjs
COPY workspace-screenshot workspace-browser-start workspace-dev-login git-remote-smartdev workspace-git-credential workspace-tools-check /usr/local/bin/
RUN ln -s /usr/local/bin/workspace-screenshot /usr/local/bin/screenshot \
 && git config --system credential.https://github.com.useHttpPath true \
 && git config --system --add credential.https://github.com.helper /usr/local/bin/workspace-git-credential
COPY start-project git-sync codex-task codex-governance-hook command_api.py checkpoints.py project_context.py /usr/local/bin/
RUN chmod 755 /usr/local/bin/git-sync /usr/local/bin/start-project /usr/local/bin/codex-task /usr/local/bin/codex-governance-hook
RUN { dpkg-query -W; node --version; codex --version; code-server --version; \
      sha256sum /usr/local/bin/codex-task /usr/local/bin/codex-governance-hook /usr/local/bin/checkpoints.py /usr/local/bin/project_context.py /etc/codex/requirements.toml; } \
    | sha256sum | cut -d' ' -f1 > /opt/command-environment-id
USER node
WORKDIR /home/node
