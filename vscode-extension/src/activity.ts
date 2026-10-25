import type * as vscode from "vscode";
import type { components } from "./lib/schema";

/** A finished build, as sent by the server's /api/activity WebSocket. */
export type BuildActivity = components["schemas"]["BuildActivity"];

const RETRY_MIN_MS = 2_000;
const RETRY_MAX_MS = 30_000;

/**
 * Follows builds across all designs (including ones a coding agent triggered) over the server's
 * /api/activity WebSocket, so the extension can bring the right panel up. Reconnects with backoff
 * while the server is down.
 */
export class ActivityWatcher implements vscode.Disposable {
  private socket: WebSocket | null = null;
  private retry: NodeJS.Timeout | undefined;
  private delay = RETRY_MIN_MS;
  private disposed = false;

  constructor(
    private readonly baseUrl: () => string,
    private readonly onBuild: (build: BuildActivity) => void,
  ) {}

  start(): void {
    this.connect();
  }

  private connect(): void {
    const url = new URL("/api/activity", this.baseUrl());
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      this.delay = RETRY_MIN_MS;
    };
    socket.onmessage = (msg: MessageEvent) => this.onBuild(JSON.parse(String(msg.data)) as BuildActivity);
    // Node's WebSocket fires only `error`, never `close`, when the connection is refused: either one reconnects, once.
    const reconnect = () => {
      if (this.disposed || this.socket !== socket) return;
      this.socket = null;
      socket.close();
      this.retry = setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, RETRY_MAX_MS);
    };
    socket.onerror = reconnect;
    socket.onclose = reconnect;
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.retry);
    this.socket?.close();
  }
}
