/** Typed client for the hitlcad server (global fetch, Node 18+). */

export interface DesignInfo {
  id: string;
  path: string;
  name: string;
  backend: string;
  revisions: number;
  open_pins: number;
  building: boolean;
  error: string | null;
}

interface OpenedDesign extends DesignInfo {
  created: boolean;
  files: string[];
}

interface BackendInfo {
  name: string;
  title: string;
  summary: string;
  available: boolean;
  detail: string;
}

interface Capabilities {
  version: string;
  backends: BackendInfo[];
  mcp_url: string;
}

interface Revision {
  id: number;
  summary: string;
  report: { status: "succeeded" | "failed"; error: string | null; duration_s: number };
}

interface ProjectResult {
  path: string;
  created: string[];
}

/** The permission-gated parts of a project's agent setup: the prompt hooks, and Codex's config.toml. */
export interface AgentSetup {
  hooks: boolean;
  codex: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export class Api {
  constructor(private readonly baseUrl: () => string) {}

  async health(timeoutMs = 1500): Promise<boolean> {
    try {
      const res = await fetch(`${this.base()}/api/health`, { signal: AbortSignal.timeout(timeoutMs) });
      return res.ok;
    } catch {
      return false;
    }
  }

  capabilities = () => this.request<Capabilities>("GET", "/api/capabilities");
  designs = () => this.request<DesignInfo[]>("GET", "/api/designs");
  openDesign = (path: string, create?: { name: string; backend: string }) =>
    this.request<OpenedDesign>("POST", "/api/designs", { path, create: create ?? null });
  buildDesign = (id: string) => this.request<Revision>("POST", `/api/designs/${encodeURIComponent(id)}/build`, undefined, 600_000);
  /** Create or update a design project; `allowed` is what the human gave permission for. */
  createProject = (path: string, allowed: AgentSetup) => this.request<ProjectResult>("POST", "/api/projects", { path, ...allowed });
  agentSetup = (path: string) => this.request<AgentSetup>("GET", `/api/projects/agents?path=${encodeURIComponent(path)}`);
  /** Selections and open pins of the designs under `root`, as chat text ("" when there are none). */
  context = (root: string) => this.send("GET", `/api/context?root=${encodeURIComponent(root)}`);

  private base(): string {
    return this.baseUrl().replace(/\/+$/, "");
  }

  private async request<T>(method: string, path: string, body?: unknown, timeoutMs = 60_000): Promise<T> {
    return JSON.parse(await this.send(method, path, body, timeoutMs)) as T;
  }

  private async send(method: string, path: string, body?: unknown, timeoutMs = 60_000): Promise<string> {
    let res: Response;
    try {
      res = await fetch(this.base() + path, {
        method,
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (e) {
      throw new ApiError(0, `Cannot reach the hitlcad server at ${this.base()}: ${(e as Error).message}`);
    }
    const text = await res.text();
    if (!res.ok) {
      let detail = text || res.statusText;
      try {
        const parsed = JSON.parse(text) as { detail?: unknown };
        if (typeof parsed.detail === "string") detail = parsed.detail;
        else if (parsed.detail) detail = JSON.stringify(parsed.detail);
      } catch {
        // keep the raw text
      }
      throw new ApiError(res.status, detail);
    }
    return text;
  }
}
