import { fileUrl } from "../api/client";
import type { Revision } from "../api/types";
import { bytes, mm } from "../lib/format";

const MARK = { pass: "✓", fail: "✗", unverified: "?" } as const;
const ROW = { pass: "pass", fail: "miss sev-error", unverified: "miss sev-info" } as const;

const DOWNLOADS: Record<string, string> = {
  exchange: "STEP for CAD tools",
  mesh: "STL for printing",
  part_exchange: "STEP of one part",
  part_mesh: "STL of one part, for printing",
  native: "Blender file",
  source: "Model source",
  log: "Build log",
};

export function ReportPanel({ designId, revision }: { designId: string; revision: Revision | null }) {
  if (!revision) return <p className="muted pad">The build report appears after the first build.</p>;
  const { report } = revision;
  const m = report.metrics;
  const preview = report.artifacts.find((a) => a.role === "preview");
  const failing = report.checks.filter((c) => !c.passed);
  const met = report.requirements.filter((r) => r.status === "pass").length;
  return (
    <div className="report">
      <p>
        <strong className={report.status === "succeeded" ? "ok" : "fail"}>
          Revision {revision.id} {report.status === "succeeded" ? "built" : "failed"}
        </strong>{" "}
        <span className="muted">
          in {report.duration_s.toFixed(1)} s with {report.backend}
          {revision.origin === "parameter_edit" ? `, your parameter edit of revision ${revision.parent}` : ""}
        </span>
      </p>
      {revision.summary && <p>{revision.summary}</p>}
      {report.error && <pre className="error-block">{report.error}</pre>}
      {report.log_tail && (
        <details>
          <summary>Build log</summary>
          <pre className="code">{report.log_tail}</pre>
        </details>
      )}

      {m && (
        <table className="metrics">
          <tbody>
            <tr><th>Size</th><td>{m.size.map((v) => v.toFixed(2)).join(" × ")} mm</td></tr>
            <tr><th>Lowest point</th><td>{mm(m.bbox_min[2])}</td></tr>
            <tr><th>Volume</th><td>{m.volume === null ? "Unknown, mesh is open" : `${m.volume.toFixed(1)} mm³`}</td></tr>
            <tr><th>Surface</th><td>{m.surface_area.toFixed(1)} mm²</td></tr>
            <tr><th>Bodies</th><td>{m.bodies}</td></tr>
            <tr><th>Triangles</th><td>{m.triangles.toLocaleString()}</td></tr>
          </tbody>
        </table>
      )}

      {report.parts.length > 0 && (
        <>
          <h3>Parts, {report.parts.length}</h3>
          <table className="metrics">
            <tbody>
              {report.parts.map((p) => (
                <tr key={p.name}>
                  <th>{p.name}</th>
                  <td>
                    {p.size.map((v) => v.toFixed(1)).join(" × ")} mm
                    <span className="muted"> {p.volume === null ? "volume unknown" : `${p.volume.toFixed(0)} mm³`}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {report.requirements.length > 0 && (
        <>
          <h3>
            Requirements, {met} of {report.requirements.length} met
          </h3>
          <ul className="checks">
            {report.requirements.map((r) => (
              <li key={r.text} className={ROW[r.status]}>
                <span className="check-mark" aria-label={r.status}>
                  {MARK[r.status]}
                </span>
                <span>
                  <span className="check-name">{r.text}</span>
                  <span className="muted"> {r.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {report.checks.length > 0 && (
        <>
          <h3>Checks{failing.length ? `, ${failing.length} need attention` : ", all passed"}</h3>
          <ul className="checks">
            {report.checks.map((c) => (
              <li key={c.name} className={c.passed ? "pass" : `miss sev-${c.severity}`}>
                <span className="check-mark" aria-label={c.passed ? "passed" : c.severity}>{c.passed ? "✓" : c.severity === "info" ? "i" : "!"}</span>
                <span>
                  <span className="check-name">{c.name.replace(/_/g, " ")}</span>
                  <span className="muted"> {c.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {preview && (
        <figure className="preview">
          <img src={fileUrl(designId, revision.id, preview.name)} alt={`Rendered views of revision ${revision.id}`} />
          <figcaption className="muted tiny">Renders your agent sees after each build: iso, front, right and top.</figcaption>
        </figure>
      )}

      <h3>Files</h3>
      <ul className="files">
        {report.artifacts.filter((a) => a.role in DOWNLOADS).map((a) => (
          <li key={a.name}>
            <a href={fileUrl(designId, revision.id, a.name)} download={a.name}>{a.name}</a>
            <span className="muted"> {DOWNLOADS[a.role]}, {bytes(a.bytes)}</span>
          </li>
        ))}
      </ul>

      <details>
        <summary>Source code</summary>
        <pre className="code">{revision.spec.source}</pre>
      </details>
    </div>
  );
}
