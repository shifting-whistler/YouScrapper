const { app, BrowserWindow, shell, ipcMain, screen, dialog } = require('electron');
const { spawn } = require('child_process');
const net = require('net');
const http = require('http');
const path = require('path');
const fs = require('fs');

const APP_NAME = 'YouScraper';
const IS_DEV = process.env.YOUSCRAPER_DEV === '1';
let backend = null;
let mainWindow = null;
let backendPort = null;
let saveBoundsTimer = null;
let logFile = null;

function initLogging() {
  try {
    const dir = app.getPath('logs');
    fs.mkdirSync(dir, { recursive: true });
    logFile = path.join(dir, 'startup.log');
    fs.appendFileSync(logFile, `\n\n=== ${new Date().toISOString()} ===\n`, 'utf8');
  } catch (_) {}
}

function log(message, error = null) {
  const line = `[${new Date().toISOString()}] ${message}${error ? `\n${error.stack || error}` : ''}\n`;
  try { if (logFile) fs.appendFileSync(logFile, line, 'utf8'); } catch (_) {}
  if (!app.isPackaged) console.error(line);
}

process.on('uncaughtException', (err) => {
  log('Uncaught exception', err);
  try { dialog.showErrorBox(APP_NAME, `${err.message || err}\n\nStartup log: ${logFile || 'unavailable'}`); } catch (_) {}
  app.quit();
});
process.on('unhandledRejection', (reason) => log('Unhandled rejection', reason instanceof Error ? reason : new Error(String(reason))));

function loadWindowBounds() {
  const fallback = { width: 1180, height: 820 };
  try {
    const file = path.join(app.getPath('userData'), 'window.json');
    const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!saved || !Number.isFinite(saved.width) || !Number.isFinite(saved.height)) return fallback;
    const display = screen.getAllDisplays().find(d => d.bounds.x <= saved.x && saved.x < d.bounds.x + d.bounds.width && d.bounds.y <= saved.y && saved.y < d.bounds.y + d.bounds.height) || screen.getPrimaryDisplay();
    const area = display.workArea;
    const width = Math.max(760, Math.min(saved.width, area.width));
    const height = Math.max(620, Math.min(saved.height, area.height));
    const x = Math.max(area.x, Math.min(saved.x, area.x + area.width - width));
    const y = Math.max(area.y, Math.min(saved.y, area.y + area.height - height));
    return { x, y, width, height };
  } catch (_) { return fallback; }
}

function persistBounds(bounds) {
  try {
    const file = path.join(app.getPath('userData'), 'window.json');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(bounds), 'utf8');
  } catch (_) {}
}

function saveWindowBounds() {
  if (!mainWindow || mainWindow.isMinimized() || mainWindow.isMaximized()) return;
  const bounds = mainWindow.getBounds();
  clearTimeout(saveBoundsTimer);
  saveBoundsTimer = setTimeout(() => persistBounds(bounds), 200);
}

ipcMain.handle('open-external', async (_event, url) => {
  if (typeof url !== 'string') return false;
  if (/^(https?:\/\/|mailto:)/i.test(url)) {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

function pickPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function backendExecutable() {
  const candidates = [];
  if (IS_DEV) {
    candidates.push(path.join(__dirname, '..', 'build', 'windows', 'venv', 'Scripts', 'python.exe'));
  } else if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, 'backend', 'YouScraperBackend.exe'));
    candidates.push(path.join(process.resourcesPath, 'YouScraperBackend', 'YouScraperBackend.exe'));
    candidates.push(path.join(process.resourcesPath, 'YouScraperBackend.exe'));
  } else {
    candidates.push(path.join(__dirname, '..', 'build', 'windows', 'backend', 'YouScraperBackend.exe'));
  }
  return candidates.find(p => fs.existsSync(p)) || candidates[0];
}

function startBackend(port) {
  if (IS_DEV) {
    const python = backendExecutable();
    if (!fs.existsSync(python)) {
      throw new Error(`Development Python environment is missing. Expected:\n${python}\n\nRun DEV.bat again to create the development environment.`);
    }
    const projectRoot = path.join(__dirname, '..');
    const backendDir = projectRoot;
    const appPy = path.join(projectRoot, 'app.py');
    if (!fs.existsSync(appPy)) {
      throw new Error(`Development backend entry point is missing. Expected:\n${appPy}`);
    }
    const deno = path.join(projectRoot, 'build', 'windows', 'deno', 'deno.exe');
    log(`Starting development backend: ${python}`);
    log(`Development backend working directory: ${backendDir}`);
    log(`Deno path: ${deno}`);
    backend = spawn(python, [appPy, `--port=${port}`], {
      cwd: backendDir,
      windowsHide: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        YOUTUBESCRAPER_PORT: String(port),
        DENO_EXE: fs.existsSync(deno) ? deno : '',
        YOUTUBESCRAPER_EXPORT_DIR: path.join(app.getPath('userData'), 'exports')
      }
    });
    backend.stdout.on('data', data => { const text = String(data); log(`BACKEND STDOUT: ${text.trimEnd()}`); });
    backend.stderr.on('data', data => { const text = String(data); log(`BACKEND STDERR: ${text.trimEnd()}`); });
    backend.on('error', err => log('Backend process error', err));
    backend.on('exit', (code, signal) => log(`Backend exited. code=${code} signal=${signal}`));
    return;
  }

  const exe = backendExecutable();
  if (!fs.existsSync(exe)) {
    throw new Error(`Bundled YouScraper backend is missing. Expected:\n${exe}`);
  }

  const backendDir = path.dirname(exe);
  const deno = app.isPackaged
    ? path.join(process.resourcesPath, 'backend', 'deno.exe')
    : path.join(__dirname, '..', 'build', 'windows', 'deno', 'deno.exe');

  log(`Starting backend: ${exe}`);
  log(`Backend working directory: ${backendDir}`);
  log(`Deno path: ${deno}`);

  backend = spawn(exe, [`--port=${port}`], {
    cwd: backendDir,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      YOUTUBESCRAPER_PORT: String(port),
      DENO_EXE: deno,
      YOUTUBESCRAPER_EXPORT_DIR: path.join(app.getPath('userData'), 'exports')
    }
  });

  backend.stdout.on('data', data => log(`BACKEND STDOUT: ${String(data).trimEnd()}`));
  backend.stderr.on('data', data => log(`BACKEND STDERR: ${String(data).trimEnd()}`));
  backend.on('error', err => log('Backend process error', err));
  backend.on('exit', (code, signal) => log(`Backend exited. code=${code} signal=${signal}`));
}

function requestHealth(port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/api/health`, res => {
      res.resume();
      if (res.statusCode === 200) resolve();
      else reject(new Error(`Backend health endpoint returned HTTP ${res.statusCode}`));
    });
    req.on('error', reject);
    req.setTimeout(1200, () => { req.destroy(); reject(new Error('Backend health check timed out')); });
  });
}

async function waitForBackend(port) {
  const started = Date.now();
  let lastError = null;
  while (Date.now() - started < 60000) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`The bundled backend exited during startup (code ${backend.exitCode}).\n\nCheck the startup log for details.`);
    }
    try {
      await requestHealth(port);
      log('Backend health check passed.');
      return;
    } catch (err) {
      lastError = err;
      await new Promise(r => setTimeout(r, 150));
    }
  }
  throw new Error(`YouScraper backend could not be started.\n\nLast health check: ${lastError ? lastError.message : 'unknown'}\n\nStartup log: ${logFile || 'unavailable'}`);
}

function createWindow() {
  const bounds = loadWindowBounds();
  mainWindow = new BrowserWindow({
    title: APP_NAME,
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    minWidth: 760,
    minHeight: 620,
    show: false,
    backgroundColor: '#0a0b0e',
    autoHideMenuBar: true,
    icon: path.join(__dirname, 'youscraper.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: !app.isPackaged
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = url.startsWith(`http://127.0.0.1:${backendPort}/`) || url.startsWith(`http://localhost:${backendPort}/`);
    if (!allowed) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) shell.openExternal(url);
    }
  });
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log(`Renderer process exited: ${JSON.stringify(details)}`);
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('resize', saveWindowBounds);
  mainWindow.on('move', saveWindowBounds);
  mainWindow.on('close', () => {
    clearTimeout(saveBoundsTimer);
    saveBoundsTimer = null;
    try { persistBounds(mainWindow.getBounds()); } catch (err) { log('Could not persist final window bounds', err); }
  });
  mainWindow.on('closed', () => {
    clearTimeout(saveBoundsTimer);
    saveBoundsTimer = null;
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  initLogging();
  log(`Launching ${APP_NAME} ${app.getVersion()} packaged=${app.isPackaged}`);
  log(`Executable: ${process.execPath}`);
  log(`Resources: ${process.resourcesPath}`);

  backendPort = await pickPort();
  log(`Selected backend port: ${backendPort}`);

  // Show the real desktop window immediately. The packaged Python backend may
  // take several seconds to unpack/start, especially on the first launch.
  createWindow();
  await mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  log('Startup window displayed.');

  startBackend(backendPort);
  await waitForBackend(backendPort);
  await mainWindow.loadURL(`http://127.0.0.1:${backendPort}/`);
  log('Main window loaded successfully.');
  app.on('activate', () => { if (mainWindow === null) createWindow(); });
}).catch(err => {
  log('Application startup failed', err);
  dialog.showErrorBox(APP_NAME, `${err.message || err}\n\nStartup log: ${logFile || 'unavailable'}`);
  app.quit();
});

let isQuitting = false;
app.on('before-quit', () => {
  isQuitting = true;
  if (backend) {
    const child = backend;
    backend = null;
    try {
      if (child.exitCode === null && !child.killed) child.kill();
    } catch (err) {
      log('Backend shutdown warning', err);
    }
  }
});

app.on('window-all-closed', () => app.quit());
