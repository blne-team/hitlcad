import type { Revision } from "../api/types";

/** The bill of materials of the revision on screen: made parts, bought parts and components, indented. */
export function BomPanel({ revision }: { revision: Revision | null }) {
  if (!revision) return <p className="muted pad">The bill of materials appears after the first build.</p>;
  const lines = revision.report.bom;
  if (!lines.length) return <p className="muted pad">Nothing is placed yet.</p>;
  const bought = lines.filter((l) => l.kind === "catalog");
  return (
    <div className="bom">
      <p className="muted">
        {lines.filter((l) => l.kind === "made").reduce((n, l) => n + l.quantity, 0)} made and{" "}
        {bought.reduce((n, l) => n + l.quantity, 0)} bought pieces in revision {revision.id}.
      </p>
      <table className="metrics bom-table">
        <thead>
          <tr>
            <th>Part</th>
            <th>Qty</th>
            <th>What</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.path} className={`bom-${l.kind}`}>
              <th style={{ paddingLeft: `${0.4 + l.level * 1.1}em` }} title={l.path}>
                {l.name}
                {l.number && <span className="muted tiny"> {l.number}</span>}
              </th>
              <td>{l.quantity}</td>
              <td>
                {l.kind === "catalog" ? (
                  <>
                    {l.designation}
                    {(l.supplier || l.mpn) && <span className="muted"> {[l.supplier, l.mpn].filter(Boolean).join(" ")}</span>}
                  </>
                ) : l.kind === "component" ? (
                  <span className="muted">component</span>
                ) : (
                  <span>{[l.material, l.process].filter(Boolean).join(", ") || <span className="muted">no material</span>}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
