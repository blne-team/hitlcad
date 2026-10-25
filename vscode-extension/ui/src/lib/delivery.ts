import type { DesignState } from "../api/types";

const AGENTS: Record<string, string> = { claude: "Claude Code", codex: "Codex" };

/**
 * Whether the agent has what the human points at in this design. The agents' prompt hook adds the
 * selection and open pins to the next message and records the delivery; this compares the latest
 * delivery (to any session) with what is pointed at now. Null when nothing is pointed at.
 */
export function deliveryStatus(state: DesignState): { sent: boolean; text: string } | null {
  const selection = state.selection.map((i) => i.id);
  const pins = Object.fromEntries(state.pins.filter((p) => !p.resolved).map((p) => [p.id, p.note]));
  if (!selection.length && !Object.keys(pins).length) return null;
  const last = Object.values(state.deliveries).sort((a, b) => b.seq - a.seq)[0];
  const same =
    last !== undefined &&
    JSON.stringify(last.selection) === JSON.stringify(selection) &&
    JSON.stringify(Object.entries(last.pins).sort()) === JSON.stringify(Object.entries(pins).sort());
  if (!same) return { sent: false, text: "Goes to your agent with your next message" };
  const at = new Date(last.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return { sent: true, text: `Sent to ${AGENTS[last.agent] ?? last.agent} at ${at}` };
}
