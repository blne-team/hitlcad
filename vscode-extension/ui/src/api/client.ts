import { server } from "../lib/host";
import type { Capabilities, DesignEvent, DesignState, ParamValue, Pick, Pin, Revision, SelectionItem } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(server + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const send = <T>(method: string, path: string, body: unknown) => request<T>(path, { method, body: JSON.stringify(body) });

export const api = {
  capabilities: () => request<Capabilities>("/api/capabilities"),
  design: (id: string) => request<DesignState>(`/api/designs/${id}`),
  setSelection: (id: string, revision: number | null, items: Pick[]) =>
    send<{ items: SelectionItem[] }>("PUT", `/api/designs/${id}/selection`, { revision, items }),
  addPin: (id: string, pin: Pick & { revision: number | null; note?: string }) => send<Pin>("POST", `/api/designs/${id}/pins`, pin),
  updatePin: (id: string, pinId: string, changes: { note?: string; resolved?: boolean }) =>
    send<Pin>("PATCH", `/api/designs/${id}/pins/${pinId}`, changes),
  removePin: (id: string, pinId: string) => request<void>(`/api/designs/${id}/pins/${pinId}`, { method: "DELETE" }),
  editParameters: (id: string, values: Record<string, ParamValue>) =>
    send<Revision>("POST", `/api/designs/${id}/parameters`, { values }),
  describeFile: (path: string, pick: Pick) => send<SelectionItem>("POST", "/api/files/describe", { path, ...pick }),
};

export const fileUrl = (designId: string, revision: number, name: string) =>
  `${server}/api/designs/${designId}/revisions/${revision}/files/${encodeURIComponent(name)}`;

export const fileCadUrl = (path: string) => `${server}/api/files/cad?path=${encodeURIComponent(path)}`;

const RETRY_MIN_MS = 2_000;
const RETRY_MAX_MS = 30_000;

/**
 * Subscribe to a design's event stream, a WebSocket: unlike a long-lived HTTP response it does not take one
 * of the browser's six connections per server, which every panel open on a design would otherwise hold.
 * When the connection closes (server restarted, tunnel down), a new one is opened with backoff, resuming
 * after the last event seen.
 */
export function subscribe(designId: string, onEvent: (event: DesignEvent) => void, onConnection: (open: boolean) => void): () => void {
  let socket: WebSocket;
  let after = 0;
  let delay = RETRY_MIN_MS;
  let retry: number | undefined;
  let stopped = false;
  const open = () => {
    const url = new URL(`/api/designs/${designId}/stream?after=${after}`, server);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(url);
    socket.onopen = () => {
      delay = RETRY_MIN_MS;
      onConnection(true);
    };
    socket.onmessage = (msg: MessageEvent<string>) => {
      try {
        const event = JSON.parse(msg.data) as DesignEvent;
        after = event.seq;
        onEvent(event);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = () => {
      if (stopped) return;
      onConnection(false);
      retry = window.setTimeout(open, delay);
      delay = Math.min(delay * 2, RETRY_MAX_MS);
    };
  };
  open();
  return () => {
    stopped = true;
    window.clearTimeout(retry);
    socket.close();
  };
}
