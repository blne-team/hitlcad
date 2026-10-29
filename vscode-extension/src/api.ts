/** Typed client for the hitlcad server (global fetch, Node 18+). */

import { errorDetail } from "./lib/errorDetail";
import type { components } from "./lib/schema";
import type { RevisionFile } from "./lib/uiState";

type Schemas = components["schemas"];

export type DesignInfo = Schemas["DesignSummary"];
type OpenedDesign = Schemas["OpenedDesign"];
type Capabilities = Schemas["Capabilities"];
type Revision = Schemas["Revision"];
type ProjectCreated = Schemas["ProjectCreated"];
/** The permission-gated parts of a project's agent setup (the agents' hooks, Codex's config.toml) and their files. */
export type AgentSetup = Schemas["AgentSetup"];
/** What the human allowed of the agent setup. */
export type Allowed = Pick<AgentSetup, "hooks" | "codex">;

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
      const res = await fetch(`${this.baseUrl()}/api/health`, { signal: AbortSignal.timeout(timeoutMs) });
      return res.ok;
    } catch {
      return false;
    }
  }

  capabilities = () => this.request<Capabilities>("GET", "/api/capabilities");
  designs = () => this.request<DesignInfo[]>("GET", "/api/designs");
  openDesign = (path: string, create?: { name: string; backend: string }) =>
    this.request<OpenedDesign>("POST", "/api/designs", { path, create: create ?? null });
  /** Waits as long as the build takes: the server ends it at its own deadline. */
  buildDesign = (id: string) => this.request<Revision>("POST", `/api/designs/${encodeURIComponent(id)}/build`, undefined, null);
  /** Create or update a design project; `allowed` is what the human gave permission for. */
  createProject = (path: string, allowed: Allowed) => this.request<ProjectCreated>("POST", "/api/projects", { path, ...allowed });
  agentSetup = (path: string) => this.request<AgentSetup>("GET", `/api/projects/agents?path=${encodeURIComponent(path)}`);
  /** Selections and open pins of the designs under `root`, as chat text ("" when there are none). */
  context = (root: string) => this.send("GET", `/api/context?root=${encodeURIComponent(root)}`).then((res) => res.text());
  /** A revision's file, by its path in the revision. */
  revisionFile = async (file: RevisionFile) => {
    const path = file.path.split("/").map(encodeURIComponent).join("/");
    const res = await this.send("GET", `/api/designs/${encodeURIComponent(file.design)}/revisions/${file.revision}/files/${path}`);
    return new Uint8Array(await res.arrayBuffer());
  };

  private async request<T>(method: string, path: string, body?: unknown, timeoutMs: number | null = 60_000): Promise<T> {
    return (await (await this.send(method, path, body, timeoutMs)).json()) as T;
  }

  /** `timeoutMs` null waits for the answer however long it takes. */
  private async send(method: string, path: string, body?: unknown, timeoutMs: number | null = 60_000): Promise<Response> {
    let res: Response;
    try {
      res = await fetch(this.baseUrl() + path, {
        method,
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: timeoutMs === null ? undefined : AbortSignal.timeout(timeoutMs),
      });
    } catch (e) {
      // fetch fails with a TypeError when there is no connection; a timeout is the server being slow
      if (!(e instanceof TypeError)) throw e;
      throw new ApiError(0, `Cannot reach the hitlcad server at ${this.baseUrl()}: ${e.message}`);
    }
    if (!res.ok) throw new ApiError(res.status, errorDetail(await res.text(), res.statusText));
    return res;
  }
}
