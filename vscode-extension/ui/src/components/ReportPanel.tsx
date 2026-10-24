import { fileUrl } from "../api/client";
import type { Result, Revision } from "../api/types";
import { bytes } from "../lib/format";

export const MARK = { pass: "✓", fail: "✗", unverified: "?" } as const;
const ROW = { pass: "pass", fail: "miss sev-error", unverified: "miss sev-info" } as const;

/** The analyses in the order a reader checks them: does it hold together, do the parts meet, can it be made. */
const ORDER = ["structure", "part", "connector", "mate", "relation", "fastener", "coverage", "interference", "motion", "manufacturing", "requirement"];
const TITLES: Record<string, string> = {
  structure: "Structure", part: "Parts", connector: "Connectors", mate: "Mates", relation: "Relations",
  fastener: "Fastener sets", coverage: "Coverage", interference: "Interference", motion: "Motion",
  manufacturing: "Manufacturing", requirement: "Requirements",
};

const DOWNLOADS: Record<string, string> = {
  exchange: "STEP of the assembly, for CAD tools",
  mesh: "STL of the assembly",
  part_exchange: "STEP of one part, in its own frame",
  part_mesh: "STL of one part, in its own frame (as it is made)",
  objects: "The part's named objects (glTF)",
  bom: "Bill of materials",
  "3mf": "3MF for printing",
  urdf: "URDF robot description",
  sdf: "SDFormat model",
  log: "Build log",
};

const order = { fail: 0, unverified: 1, pass: 2 } as const;

function grouped(results: Result[]): [string, Result[]][] {
  const groups = new Map<string, Result[]>();
  for (const r of results) groups.set(r.analysis, [...(groups.get(r.analysis) ?? []), r]);
  const known = ORDER.filter((a) => groups.has(a));
  const rest = [...groups.keys()].filter((a) => !ORDER.includes(a)).sort();
  return [...known, ...rest].map((a) => [a, [...groups.get(a)!].sort((x, y) => order[x.status] - order[y.status])]);
}

function ResultList({ results }: { results: Result[] }) {
  return (
    <ul className="checks">
      {results.map((r, i) => (
        <li key={`${r.subject}-${i}`} className={r.status === "pass" ? "pass" : r.severity === "warning" ? "miss sev-warning" : ROW[r.status]}>
          <span className="check-mark" aria-label={r.status}>
            {MARK[r.status]}
          </span>
          <span>
            <span className="check-name">{r.subject}</span>
            <span className="muted"> {r.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A revision's build: what every analysis found, the parts, and the files. */
export function ReportPanel({ designId, revision }: { designId: string; revision: Revision | null }) {
  if (!revision) return <p className="muted pad">The build report appears after the first build.</p>;
  const { report } = revision;
  const m = report.metrics;
  const preview = report.artifacts.find((a) => a.role === "preview");
  const failing = report.results.filter((r) => r.status === "fail").length;
  return (
    <div className="report">
      <p>
        <strong className={report.status === "succeeded" ? "ok" : "fail"}>
          Revision {revision.id} {report.status === "succeeded" ? "built" : "failed"}
        </strong>{" "}
        <span className="muted">
          in {report.duration_s.toFixed(1)} s{report.configuration ? `, configuration ${report.configuration}` : ""}
          {revision.origin === "parameter_edit" ? ", from your parameter edit" : ""}
          {report.results.length ? ` · ${failing ? `${failing} failing` : "nothing failing"} of ${report.results.length} results` : ""}
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

      {grouped(report.results).map(([analysis, results]) => {
        const bad = results.filter((r) => r.status !== "pass").length;
        return (
          <details key={analysis} className="result-group" open={results.some((r) => r.status === "fail")}>
            <summary>
              <span className="check-name">{TITLES[analysis] ?? analysis}</span>
              <span className="muted"> {bad ? `${bad} of ${results.length} need attention` : `${results.length} pass`}</span>
            </summary>
            <ResultList results={results} />
          </details>
        );
      })}

      {m && (
        <table className="metrics">
          <tbody>
            <tr><th>Assembly</th><td>{m.size.map((v) => v.toFixed(2)).join(" × ")} mm, {report.instances.length} instances</td></tr>
            {report.mass && <tr><th>Mass</th><td>{report.mass.mass_g.toFixed(1)} g, centre ({report.mass.centre_mm.map((v) => v.toFixed(1)).join(", ")}) mm</td></tr>}
          </tbody>
        </table>
      )}

      {report.parts.length > 0 && (
        <>
          <h3>Parts, {report.parts.length}</h3>
          <table className="metrics parts">
            <tbody>
              {report.parts.map((p) => (
                <tr key={p.name}>
                  <th title={p.file}>{p.name}</th>
                  <td>
                    {p.metrics.size.map((v) => v.toFixed(1)).join(" × ")} mm
                    {p.mass && <span> · {p.mass.mass_g.toFixed(1)} g</span>}
                    <span className="muted">
                      {" "}
                      {p.made ? [p.material, p.process].filter(Boolean).join(", ") : p.catalog?.designation ?? "catalog"}
                      {p.cached ? " · from the cache" : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {Object.keys(report.components).length > 0 && (
        <>
          <h3>Components</h3>
          <ul className="checks">
            {Object.entries(report.components).map(([name, child]) => {
              const bad = child.results.filter((r) => r.status === "fail").length;
              return (
                <li key={name} className={child.status === "succeeded" && !bad ? "pass" : "miss sev-error"}>
                  <span className="check-mark">{child.status === "succeeded" && !bad ? "✓" : "✗"}</span>
                  <span>
                    <span className="check-name">{name}</span>
                    <span className="muted"> {child.parts.length} parts, {bad ? `${bad} failing results in its own build` : "its own build verifies"}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {preview && (
        <figure className="preview">
          <img src={fileUrl(designId, revision.id, preview.path)} alt={`Rendered views of revision ${revision.id}`} />
          <figcaption className="muted tiny">Renders your agent sees after each build: iso, front, right and top.</figcaption>
        </figure>
      )}

      <h3>Files</h3>
      <ul className="files">
        {report.artifacts.filter((a) => a.role in DOWNLOADS).map((a) => (
          <li key={a.path}>
            <a href={fileUrl(designId, revision.id, a.path)} download={a.name}>{a.path}</a>
            <span className="muted"> {DOWNLOADS[a.role]}, {bytes(a.bytes)}</span>
          </li>
        ))}
      </ul>

      <details>
        <summary>Source code</summary>
        {Object.entries(revision.spec.sources).map(([path, text]) => (
          <details key={path} className="source-file">
            <summary className="muted">{path}</summary>
            <pre className="code">{text}</pre>
          </details>
        ))}
      </details>
    </div>
  );
}
