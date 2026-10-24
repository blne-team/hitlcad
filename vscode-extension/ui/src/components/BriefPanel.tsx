import type { DesignBrief } from "../api/types";

const LISTS: { key: "features" | "assumptions" | "open_questions"; label: string }[] = [
  { key: "features", label: "Features" },
  { key: "assumptions", label: "Assumptions" },
  { key: "open_questions", label: "Open questions" },
];

/** The brief from design.json, read-only: change it by asking your agent or editing the file. */
export function BriefPanel({ brief }: { brief: DesignBrief | null }) {
  if (!brief) {
    return (
      <p className="muted pad">
        No brief yet. Ask your agent to write one into <code>design.json</code>: what the part is for, key dimensions
        and assumptions. Requirements, verified on every build, are under Results.
      </p>
    );
  }
  return (
    <div className="brief">
      <h3>{brief.title}</h3>
      <p>{brief.summary}</p>
      {brief.key_dimensions.length > 0 && (
        <table className="metrics">
          <tbody>
            {brief.key_dimensions.map((d) => (
              <tr key={d.name}>
                <th>{d.name}</th>
                <td>
                  {d.value} {d.unit}
                  {d.note && <span className="muted"> {d.note}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {LISTS.filter((l) => brief[l.key].length > 0).map((l) => (
        <section key={l.key}>
          <h4>{l.label}</h4>
          <ul>
            {brief[l.key].map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ))}
      <p className="hint">Edit it in design.json or ask your agent; the panel updates when the file is saved.</p>
    </div>
  );
}
