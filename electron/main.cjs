const { app, BrowserWindow, ipcMain, dialog, nativeImage } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs').promises;
const fsSync = require('fs');
const os = require('os');
const path = require('path');

// 开发模式下 Dock 悬停与菜单栏的应用名默认是 "Electron"，这里显式改名。
app.setName('Agent');

function iconPath() {
  // 打包后扁平放在 Resources 根；开发模式在项目 assets/ 下。
  return path.join(projectRoot(), app.isPackaged ? 'icon.png' : 'assets/icon.png');
}

function uiRoot() {
  // 界面文件：打包后在 app.asar 内，开发模式在项目根。
  return app.isPackaged ? app.getAppPath() : projectRoot();
}

let mainWindow;
let bridge;
let bridgeBuffer = '';
const backgroundExtensions = ['png', 'jpg', 'jpeg', 'webp', 'gif'];
const backgroundMimeTypes = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  webp: 'image/webp',
  gif: 'image/gif',
};

function backgroundDirectory() {
  return path.join(app.getPath('userData'), 'appearance');
}

async function removeBackgroundFiles() {
  await Promise.all(backgroundExtensions.map(async (extension) => {
    try {
      await fs.unlink(path.join(backgroundDirectory(), `background.${extension}`));
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }));
}

function projectRoot() {
  return app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..');
}

function sendToRenderer(message) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('bridge-event', message);
  }
}

function userConfigDir() {
  // 与桥接进程的 runtime_root 一致：用户可编辑的配置与数据都在这里。
  return path.join(os.homedir(), 'Library', 'Application Support', 'Agent');
}

function prepareUserConfigs() {
  // 打包后首次启动：把包内 models/modules 配置拷到用户目录（已存在则不覆盖），
  // 之后用户直接改用户目录的文件即可自定义模型，升级 app 也不会丢。
  if (!app.isPackaged) return {};
  try {
    const dir = userConfigDir();
    fsSync.mkdirSync(dir, { recursive: true });
    const result = {};
    // 出厂配置：示例文件 → 用户目录的实际配置（已存在则不覆盖）
    const seedFiles = [
      ['models.json', 'models.example.json'],
      ['servers.json', 'servers.example.json'],
      ['modules.json', 'modules.json'],
    ];
    for (const [name, bundledName] of seedFiles) {
      const target = path.join(dir, name);
      if (!fsSync.existsSync(target)) {
        const bundled = path.join(projectRoot(), bundledName);
        if (fsSync.existsSync(bundled)) {
          console.log(`[Agent] 初始化用户配置：${target}`);
          fsSync.copyFileSync(bundled, target);
        }
      }
      if (fsSync.existsSync(target)) result[name] = target;
    }
    return result;
  } catch (error) {
    console.error('[Agent] 初始化用户配置失败：', error);
    return {};
  }
}

function startBridge() {
  const root = projectRoot();
  const python = process.env.AGENTDESK_PYTHON || path.join(root, '.venv-mcp', 'bin', 'python');
  const script = path.join(root, 'run_agentdesk_bridge.py');
  const command = app.isPackaged ? path.join(root, 'agentdesk_bridge') : python;
  const args = app.isPackaged ? [] : [script];

  bridge = spawn(command, args, {
    cwd: root,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      // 打包后配置以用户目录为准（首次启动自动从包内拷贝），改这里即可自定义模型。
      ...(app.isPackaged ? prepareUserConfigs() : {}),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  bridge.stdout.on('data', (chunk) => {
    bridgeBuffer += chunk.toString();
    const lines = bridgeBuffer.split('\n');
    bridgeBuffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        sendToRenderer(JSON.parse(line));
      } catch (error) {
        sendToRenderer({ type: 'bridge_error', message: `桥接进程返回了无法解析的消息：${error.message}` });
      }
    }
  });

  bridge.stderr.on('data', (chunk) => {
    sendToRenderer({ type: 'bridge_log', message: chunk.toString().trim() });
  });

  bridge.on('error', (error) => {
    sendToRenderer({ type: 'bridge_error', message: `无法启动 Agent 服务：${error.message}` });
  });

  bridge.on('exit', (code) => {
    if (code !== 0) sendToRenderer({ type: 'bridge_error', message: `Agent 服务已退出（代码 ${code}）。` });
  });
}

function sendToBridge(payload) {
  if (!bridge || bridge.killed) {
    sendToRenderer({ type: 'bridge_error', message: 'Agent 服务尚未启动。' });
    return;
  }
  bridge.stdin.write(`${JSON.stringify(payload)}\n`);
}

function createWindow() {
  const icon = nativeImage.createFromPath(iconPath());
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1120,
    minHeight: 720,
    backgroundColor: '#f8fbff',
    title: 'Agent',
    icon: icon.isEmpty() ? undefined : icon,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  // 图标文件的绝对路径经 query 传给渲染层：asar 内外的路径差异由主进程抹平。
  mainWindow.loadFile(path.join(uiRoot(), 'renderer', 'index.html'), {
    query: { icon: iconPath() },
  });
  mainWindow.webContents.once('did-finish-load', () => sendToBridge({ type: 'get_config' }));
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => {
  // macOS：开发模式下 dock 图标不会自动应用，需要手动设置。
  if (process.platform === 'darwin' && app.dock) {
    const dockIcon = nativeImage.createFromPath(iconPath());
    if (!dockIcon.isEmpty()) app.dock.setIcon(dockIcon);
  }
  startBridge();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (bridge && !bridge.killed) bridge.kill();
});

ipcMain.handle('files:pick', async (_event, payload = {}) => {
  const wantDirectory = Boolean(payload.directory);
  const result = await dialog.showOpenDialog({
    title: wantDirectory ? '选择文件夹' : '选择文件（可多选）',
    properties: wantDirectory ? ['openDirectory'] : ['openFile', 'multiSelections'],
  });
  if (result.canceled || !result.filePaths?.length) return { ok: false, paths: [] };
  return { ok: true, paths: result.filePaths };
});
ipcMain.on('request-config', () => sendToBridge({ type: 'get_config' }));
ipcMain.on('chat', (_event, payload) => sendToBridge({ type: 'chat', ...payload }));
ipcMain.on('cancel-chat', (_event, payload) => sendToBridge({ type: 'cancel_chat', ...payload }));
ipcMain.on('approval', (_event, payload) => sendToBridge({ type: 'approval', ...payload }));
ipcMain.on('models:get', () => sendToBridge({ type: 'get_models' }));
ipcMain.on('models:save', (_event, payload) => sendToBridge({ type: 'save_models', ...payload }));
ipcMain.on('servers:get', () => sendToBridge({ type: 'get_servers' }));
ipcMain.on('servers:save', (_event, payload) => sendToBridge({ type: 'save_servers', ...payload }));
ipcMain.on('servers:test', (_event, payload) => sendToBridge({ type: 'test_server', ...payload }));
ipcMain.on('servers:get', () => sendToBridge({ type: 'get_servers' }));
ipcMain.on('servers:save', (_event, payload) => sendToBridge({ type: 'save_servers', ...payload }));
ipcMain.on('servers:test', (_event, payload) => sendToBridge({ type: 'test_server', ...payload }));

ipcMain.handle('appearance:save-background', async (_event, payload = {}) => {
  const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$/.exec(String(payload.dataUrl || ''));
  if (!match) return { ok: false, message: '背景图片格式不受支持。' };
  const extension = match[1] === 'image/jpeg' ? 'jpg' : match[1].split('/')[1];
  await fs.mkdir(backgroundDirectory(), { recursive: true });
  await removeBackgroundFiles();
  await fs.writeFile(path.join(backgroundDirectory(), `background.${extension}`), Buffer.from(match[2], 'base64'));
  return { ok: true, fileName: String(payload.fileName || `background.${extension}`) };
});

ipcMain.handle('appearance:load-background', async () => {
  for (const extension of backgroundExtensions) {
    try {
      const buffer = await fs.readFile(path.join(backgroundDirectory(), `background.${extension}`));
      return { ok: true, dataUrl: `data:${backgroundMimeTypes[extension]};base64,${buffer.toString('base64')}` };
    } catch (error) {
      if (error.code !== 'ENOENT') return { ok: false, message: error.message };
    }
  }
  return { ok: true, dataUrl: '' };
});

ipcMain.handle('appearance:clear-background', async () => {
  await removeBackgroundFiles();
  return { ok: true };
});
