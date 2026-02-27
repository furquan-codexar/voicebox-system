import type { PlatformLifecycle } from '@/platform/types';

const DEFAULT_BACKEND_PORT = 17493;

class WebLifecycle implements PlatformLifecycle {
  onServerReady?: () => void;

  async startServer(_remote = false): Promise<string> {
    // Web: use same host as the page to avoid Private Network Access blocking.
    // When visiting http://64.247.206.73:5173, API must be http://64.247.206.73:17493,
    // not 127.0.0.1 (which browsers block from public origins).
    const port =
      import.meta.env.VITE_SERVER_PORT || String(DEFAULT_BACKEND_PORT);
    const serverUrl =
      typeof window !== 'undefined'
        ? `http://${window.location.hostname}:${port}`
        : import.meta.env.VITE_SERVER_URL || `http://localhost:${port}`;
    this.onServerReady?.();
    return serverUrl;
  }

  async stopServer(): Promise<void> {
    // No-op for web - server is managed externally
  }

  async setKeepServerRunning(_keep: boolean): Promise<void> {
    // No-op for web
  }

  async setupWindowCloseHandler(): Promise<void> {
    // No-op for web - no window close handling needed
  }
}

export const webLifecycle = new WebLifecycle();
