const vscode = require('vscode');
const { execFile, spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { createHash, randomBytes, randomUUID } = require('node:crypto');
const { createInterface } = require('node:readline');
const git = require('node:util').promisify(execFile);
const stateDirectory = path.join(os.homedir(), '.local', 'state', 'command-codex');
const idPattern = /^[a-f0-9-]{36}$/i;

async function readState(directory, name) {
  try {
    const value = (await fs.readFile(path.join(directory, name), 'utf8')).trim();
    return idPattern.test(value) ? value : undefined;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

async function saveState(directory, name, value) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const pending = path.join(directory, randomUUID());
  await fs.writeFile(pending, value, { mode: 0o600, flag: 'wx' });
  try {
    await fs.rename(pending, path.join(directory, name));
  } finally {
    await fs.rm(pending, { force: true });
  }
}

function activate(context) {
  let panel;
  context.subscriptions.push(vscode.commands.registerCommand('commandCenter.pullRequest', async () => {
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!cwd) return;
    try {
      const origin = (await git('git', ['remote', 'get-url', 'origin'], { cwd })).stdout.trim();
      const repo = /^https:\/\/github\.com\/([A-Za-z0-9-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(origin);
      if (!repo) throw new Error('The project needs an HTTPS GitHub origin.');
      const branch = (await git('git', ['symbolic-ref', '--short', 'HEAD'], { cwd })).stdout.trim();
      await git('git', ['show-ref', '--verify', 'refs/remotes/origin/' + branch], { cwd });
      await vscode.env.openExternal(vscode.Uri.parse(
        'https://github.com/' + repo[1] + '/compare/' + encodeURIComponent(branch) + '?expand=1'
      ));
    } catch {
      vscode.window.showErrorMessage('Push the current branch to the GitHub origin before opening a pull request.');
    }
  }));
  context.subscriptions.push(vscode.commands.registerCommand('commandCenter.codex', async () => {
    if (panel) {
      panel.reveal();
      return;
    }
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!folder) {
      vscode.window.showErrorMessage('Open your project folder first.');
      return;
    }
    const conversationDirectory = path.join(stateDirectory, createHash('sha256').update(folder).digest('hex'));
    const view = vscode.window.createWebviewPanel('projectCodex', 'Coding agent', vscode.ViewColumn.Beside, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [context.extensionUri]
    });
    panel = view;
    let child;
    let busy = false;
    let cancelled = false;
    let disposed = false;
    const send = (type, text) => view.webview.postMessage({ type, text });
    const cancel = () => {
      cancelled = true;
      if (child) child.kill('SIGINT');
    };
    view.onDidDispose(() => {
      disposed = true;
      cancel();
      panel = undefined;
    });
    view.webview.onDidReceiveMessage(async message => {
      if (message.type === 'cancel') {
        cancel();
        return;
      }
      if (message.type === 'ready') {
        try {
          await send('objective', await readState(conversationDirectory, 'active-objective') || '');
        } catch {
          await send('output', 'Could not load the last objective.');
        }
        return;
      }
      if (message.type === 'new' && !busy) {
        try {
          const objective = String(message.objective || '');
          if (idPattern.test(objective)) {
            await fs.rm(path.join(conversationDirectory, objective), { force: true });
            await fs.rm(path.join(conversationDirectory, objective+'.agent'), { force: true });
          }
          await send('output', 'New coding-agent conversation. The objective remains selected.');
        } catch {
          await send('output', 'Could not reset the coding-agent conversation.');
        }
        return;
      }
      if (message.type !== 'run' || busy || typeof message.prompt !== 'string' || !message.prompt.trim()) return;
      busy = true;
      cancelled = false;
      let directory;
      try {
        const files = message.files;
        if (!Array.isArray(files) || files.length > 8 ||
            files.some(file => typeof file.data !== 'string' || file.data.length > 7_000_000)) {
          throw new Error('Attach up to eight files of 5 MB each.');
        }
        const args = ['--terms', String(message.terms || '').trim().slice(0, 300) || 'project decisions', '--json'];
        const selected = String(message.objective || '').trim();
        if (selected && !idPattern.test(selected)) throw new Error('Enter an objective UUID from the portal.');
        const objective = selected || randomUUID();
        args.push('--objective', objective);
        if (!selected || context.workspaceState.get('pendingObjective') === objective) {
          await context.workspaceState.update('pendingObjective', objective);
          args.push('--new-objective');
        }
        await saveState(conversationDirectory, 'active-objective', objective);
        await send('objective', objective);
        const session = await readState(conversationDirectory, objective);
        const sessionAgent = await readState(conversationDirectory, objective+'.agent') || 'codex_cli';
        if (session) args.push('--resume', session, '--resume-agent', sessionAgent);
        if (message.criterion) args.push('--criterion', message.criterion);

        if (files.length) directory = await fs.mkdtemp(path.join(os.tmpdir(), 'codex-input-'));
        for (const [index, file] of files.entries()) {
          const extensions = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp' };
          const extension = extensions[file.type];
          let name = path.basename(String(file.name)).replace(/[^a-zA-Z0-9._-]/g, '_').slice(-120);
          const isImage = /\.(jpg|jpeg|png|webp)$/i.test(name);
          if (extension && !isImage) name += extension;
          const target = path.join(directory, index + '-' + name);
          await fs.writeFile(target, Buffer.from(file.data, 'base64'), { mode: 0o600 });
          args.push(isImage || extension ? '--image' : '--file', target);
        }
        if (cancelled || disposed) return;
        args.push('--', message.prompt);
        child = spawn('codex-task', args, { cwd: folder, stdio: ['ignore', 'pipe', 'pipe'] });
        let nextSession;
        let nextAgent='codex_cli';
        createInterface({ input: child.stdout }).on('line', line => {
          try {
            const event = JSON.parse(line);
            if (event.type === 'thread.started' && idPattern.test(event.thread_id)) {
              nextSession = event.thread_id;
              nextAgent = event.agent === 'claude_code' ? 'claude_code' : 'codex_cli';
            }
          } catch {}
          void send('output', line);
        });
        child.stderr.setEncoding('utf8').on('data', data => void send('output', data));
        const code = await new Promise((resolve, reject) => {
          child.once('error', reject);
          child.once('close', resolve);
        });
        if (nextSession) {
          await saveState(conversationDirectory, objective, nextSession);
          await saveState(conversationDirectory, objective+'.agent', nextAgent);
          await context.workspaceState.update('pendingObjective', undefined);
        }
        await send('output', 'The coding agent finished with exit status ' + code + '. Review changes in Source Control.');
      } catch (error) {
        await send('output', error.message);
      } finally {
        child = undefined;
        busy = false;
        if (directory) await fs.rm(directory, { recursive: true, force: true });
        await send('done');
      }
    });
    const nonce = randomBytes(24).toString('base64');
    const script = view.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'chat.js'));
    view.webview.html = `<!doctype html><html lang="en"><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src blob:; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style nonce="${nonce}">body{padding:12px} textarea,input[type=text]{width:100%;box-sizing:border-box;margin:6px 0} #files img{max-width:120px;max-height:90px;vertical-align:middle} pre{white-space:pre-wrap;overflow-wrap:anywhere} #files li{margin:6px 0} button{margin-right:6px}</style>
</head><body><label for="prompt">Coding-agent instructions</label>
<textarea id="prompt" rows="6" placeholder="Paste a screenshot here or drop files into this panel"></textarea>
<label for="objective">Objective ID</label><input id="objective" type="text" placeholder="Leave blank to start a new objective">
<label for="criterion">Acceptance criterion ID</label><input id="criterion" type="text" placeholder="Defaults to the next available criterion">
<label for="terms">Project memory search</label><input id="terms" type="text" maxlength="300">
<label for="upload">Attach files</label> <input id="upload" type="file" multiple>
<ul id="files"></ul><p id="status" role="status"></p><button id="run">Send</button><button id="cancel" disabled>Cancel</button><button id="new">New conversation</button>
<pre id="output" aria-label="Execution output"></pre>
<script nonce="${nonce}" src="${script}"></script></body></html>`;
  }));
}
module.exports = { activate };
