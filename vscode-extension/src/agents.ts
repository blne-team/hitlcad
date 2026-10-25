import * as path from "node:path";
import * as vscode from "vscode";
import type { AgentSetup, Allowed } from "./api";

const NEVER_ASK = "hitlcad.agentSetup.neverAsk";

/**
 * Ask the human whether hitlcad may set up the agents for the project at `dir`: the prompt hook that
 * adds their selection and pins to each message, and Codex's config. Nothing is changed without
 * this. `setup` names the files (the server's). Returns what they allowed (both false when they decline).
 */
export async function askAgentSetup(dir: string, setup: AgentSetup): Promise<Allowed> {
  const both = "Allow";
  const hooksOnly = "Allow Without Codex Config";
  const answer = await vscode.window.showInformationMessage(
    "Let Claude Code and Codex see what you select and pin in hitlcad?",
    {
      modal: true,
      detail:
        `hitlcad adds a prompt hook to ${setup.claude_hooks} in ${path.basename(dir)} (your own Claude Code settings, ` +
        `not committed) and to Codex's ${setup.codex_hooks}. Before each message you send to the agent, it adds your ` +
        "selection and open pins from the hitlcad panel; in a folder without designs it adds nothing. Codex asks you " +
        "once to approve the hook (/hooks).\n\n" +
        `For Codex it also adds hitlcad's MCP server to ${setup.codex_config}.`,
    },
    both,
    hooksOnly,
  );
  return { hooks: answer === both || answer === hooksOnly, codex: answer === both };
}

/**
 * Offer the agent setup for a hitlcad project that lacks it, once per workspace unless the human
 * says not to ask again. `setUp` runs the setup (and its own permission question).
 */
export async function offerAgentSetup(ctx: vscode.ExtensionContext, setup: AgentSetup, setUp: () => Thenable<unknown>): Promise<void> {
  if ((setup.hooks && setup.codex) || ctx.workspaceState.get<boolean>(NEVER_ASK)) return;
  const missing = setup.hooks ? "Codex does not have hitlcad's MCP server yet" : "your agents don't see your selection and pins yet";
  const choice = await vscode.window.showInformationMessage(`hitlcad: ${missing}.`, "Set Up…", "Don't Ask Again");
  if (choice === "Set Up…") await setUp();
  if (choice === "Don't Ask Again") await ctx.workspaceState.update(NEVER_ASK, true);
}
