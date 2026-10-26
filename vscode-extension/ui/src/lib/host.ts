import type { PanelConfig } from "../../../src/lib/panelHtml";

declare function acquireVsCodeApi(): { postMessage(message: unknown): void };

/** What the extension put in this webview's page: the server to talk to, what to show, and whether to say how to select. */
export const { server, view, selectionHint }: PanelConfig = JSON.parse(document.getElementById("root")!.dataset.config!);

const vscode = acquireVsCodeApi();

/** Send a message to the extension (see its src/lib/uiState.ts for the ones it reads). */
export const toExtension = (message: { type: string } & Record<string, unknown>) => vscode.postMessage(message);
