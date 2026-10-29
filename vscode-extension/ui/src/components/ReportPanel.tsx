import { fileUrl } from "../api/client";
import type { AnalysisInfo, BuildProgress, ErrorKind, Finding, Problem, Result, Revision } from "../api/types";
import { useCapabilities } from "../hooks/useCapabilities";
import { bytes } from "../lib/format";
import { toExtension } from "../lib/host";
import { affectedInstances } from "../lib/inspection";
import { OutputsChoice } from "./OutputsChoice";

export const MARK = { pass: "✓", fail: "✗", unverified: "?" } as const;
const WARNING = "!";
const order = { fail: 0, unverified: 1, pass: 2 } as const;
/** What the human did in the panel that made a revision. */
const ORIGINS: Partial<Record<Revision["origin"], string>> = {
  parameter_edit: ", from your parameter edit", outputs_edit: ", from your choice of outputs", undo: ", from your undo", redo: ", from your redo",
};

const rowClass = (r: Pick<Result, "status" | "severity">) =>
  r.status === "pass" ? "pass" : r.status === "unverified" || r.severity === "info" ? "miss sev-info" : `miss sev-${r.severity}`;

/** " ×4 at [0, 1, 2, 3]": a finding's copies, as the agent reads them. */
function copies(f: Finding): string {
  if (f.results.length === 1) return "";
  return ` ×${f.results.length}${f.indices.length ? ` at [${f.indices.join(", ")}]` : ""}`;
}

/** Where in the design's files, past what the subject says: " line 31" after a file, " (design.json)" after a
 * field of it; as the agent reads it. */
function where(r: Result): string {
  const w = r.where;
  if (!w) return "";
  const file = w.file && w.file !== r.subject ? w.file : "";
  const address = w.address && w.address !== r.subject ? w.address : "";
  const at = [file, w.line ? `line ${w.line}` : ""].filter(Boolean).join(" ");
  const text = [at, address].filter(Boolean).join(", ");
  return !text ? "" : text.startsWith("line") ? ` ${text}` : ` (${text})`;
}

/** A report's results by analysis, in the order the server runs them (others after, by name). */
function byAnalysis(results: Result[], analyses: AnalysisInfo[]): [string, Result[]][] {
  const groups = new Map<string, Result[]>();
  for (const r of results) groups.set(r.analysis, [...(groups.get(r.analysis) ?? []), r]);
  const known = analyses.map((a) => a.name).filter((a) => groups.has(a));
  const rest = [...groups.keys()].filter((a) => !known.includes(a)).sort();
  return [...known, ...rest].map((a) => [a, [...groups.get(a)!].sort((x, y) => order[x.status] - order[y.status])]);
}

function ResultList({ results }: { results: Result[] }) {
  return (
    <ul className="checks">
      {results.map((r, i) => (
        <li key={`${r.subject}-${i}`} className={rowClass(r)}>
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

/** One problem: what leads it (folded copies at their pattern indices), what else saw it, and every check in it. */
type ProblemProps = { problem: Problem; results: Result[]; title: (analysis: string) => string; kind: (code: string) => ErrorKind | undefined; onInspect?: () => void };

function ProblemRow({ problem, results, title, kind, onInspect }: ProblemProps) {
  const [lead, ...also] = problem.findings;
  const first = results[lead!.results[0]!]!;
  const all = problem.findings.flatMap((f) => f.results.map((i) => results[i]!));
  const warning = problem.status === "fail" && problem.severity === "warning";
  return (
    <li className={`problem ${rowClass({ status: problem.status, severity: problem.severity })}`}>
      <span className="check-mark" aria-label={warning ? "warning" : problem.status}>
        {warning ? WARNING : MARK[problem.status]}
      </span>
      <div>
        <span className="check-name" title={kind(first.code)?.title ?? first.code}>
          {title(first.analysis)} {first.subject}
          {where(first)}
          {copies(lead!)}
        </span>
        <span className="muted detail"> {first.detail}</span>
        {onInspect && (
          <button type="button" className="link-button inspect-finding" onClick={onInspect}>Inspect in model</button>
        )}
        {kind(first.code)?.hint && <div className="hint muted">→ {kind(first.code)!.hint}</div>}
        {also.length > 0 && (
          <div className="also muted">
            also:{" "}
            {also.map((f, k) => {
              const r = results[f.results[0]!]!;
              return (
                <span key={k}>
                  {k > 0 && ", "}
                  {title(r.analysis)} {r.subject}
                  {f.results.length > 1 ? ` ×${f.results.length}` : ""}
                </span>
              );
            })}
          </div>
        )}
        {all.length > 1 && (
          <details className="problem-checks">
            <summary className="muted">{all.length} checks</summary>
            <ResultList results={all} />
          </details>
        )}
      </div>
    </li>
  );
}

type Found = { results: Result[]; problems: Problem[]; inspect?: (problem: Problem) => (() => void) | undefined };

/** What the analyses found: the problems, then every check by analysis. */
function Findings({ results, problems, inspect }: Found) {
  const capabilities = useCapabilities();
  const analyses = capabilities?.analyses ?? [];
  const title = (name: string) => analyses.find((a) => a.name === name)?.title ?? name;
  const kind = (code: string) => capabilities?.error_kinds.find((k) => k.code === code);
  return (
    <>
      {problems.length > 0 && (
        <ul className="checks problems">
          {problems.map((p) => (
            <ProblemRow key={p.findings[0]!.results[0]} problem={p} results={results} title={title} kind={kind} onInspect={inspect?.(p)} />
          ))}
        </ul>
      )}

      {results.length > 0 && (
        <details className="all-checks">
          <summary>Every check, by analysis</summary>
          {byAnalysis(results, analyses).map(([analysis, group]) => {
            const bad = group.filter((r) => r.status !== "pass").length;
            const noted = group.filter((r) => r.severity === "info").length; // listed, but no checks
            const checks = group.length - noted;
            return (
              <details key={analysis} className="result-group">
                <summary title={analyses.find((a) => a.name === analysis)?.summary}>
                  <span className="check-name">{title(analysis)}</span>
                  <span className="muted">
                    {" "}
                    {bad ? `${bad} of ${checks} need attention` : `${checks} pass`}
                    {noted ? `, ${noted} noted` : ""}
                  </span>
                </summary>
                <ResultList results={group} />
              </details>
            );
          })}
        </details>
      )}
    </>
  );
}

/** The build running: what the analyses that have finished found, before its revision comes. */
function Checking({ building }: { building: BuildProgress }) {
  return (
    <div className="report">
      <p>
        <strong>Revision {building.revision}, being checked</strong>
        {building.doing && <span className="muted"> {building.doing}</span>}
      </p>
      {building.results.length > 0 && <p className="tally">So far: {building.tally.text}</p>}
      <Findings results={building.results} problems={building.problems} />
    </div>
  );
}

/** A revision's build: what every analysis found, the parts, and the files; while one runs, what it has found. */
export function ReportPanel({ designId, revision, building, onInspect }: {
  designId: string; revision: Revision | null; building: BuildProgress | null; onInspect?: (instances: string[]) => void;
}) {
  if (building) return <Checking building={building} />;
  if (!revision) return <p className="muted pad">The build report appears after the first build.</p>;
  const { report } = revision;
  const m = report.metrics;
  const preview = report.artifacts.find((a) => a.role === "preview");
  const inspect = (p: Problem) => {
    const instances = affectedInstances(revision, p.findings.flatMap((f) => f.results.flatMap((i) => report.results[i]!.about)));
    return onInspect && instances.length ? () => onInspect(instances) : undefined;
  };
  return (
    <div className="report">
      <p>
        <strong className={report.status === "succeeded" ? "ok" : "fail"}>
          Revision {revision.id} {report.status === "succeeded" ? "built" : "failed"}
        </strong>{" "}
        <span className="muted">
          in {report.duration_s.toFixed(1)} s{report.configuration ? `, configuration ${report.configuration}` : ""}
          {ORIGINS[revision.origin]}
        </span>
      </p>
      {report.results.length > 0 && <p className="tally">{report.tally.text}</p>}
      {revision.summary && <p>{revision.summary}</p>}

      <Findings results={report.results} problems={report.problems} inspect={inspect} />

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
              const ok = child.status === "succeeded" && !child.tally.problems;
              return (
                <li key={name} className={ok ? "pass" : "miss sev-error"}>
                  <span className="check-mark">{ok ? MARK.pass : MARK.fail}</span>
                  <span>
                    <span className="check-name">{name}</span>
                    <span className="muted"> {child.parts.length} parts; its own build: {child.tally.text}</span>
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
          <figcaption className="muted tiny">Iso, front, right and top views, written while Rendered views is chosen below.</figcaption>
        </figure>
      )}

      <h3>Files</h3>
      <ul className="files">
        {report.artifacts.filter((a) => a.label).map((a) => (
          <li key={a.path}>
            <button type="button" className="link-button" title={`Save a copy of ${a.name}`}
              onClick={() => toExtension({ type: "hitlcad.saveFile", design: designId, revision: revision.id, path: a.path, name: a.name })}>
              {a.path}
            </button>
            <span className="muted"> {a.label}, {bytes(a.bytes)}</span>
          </li>
        ))}
      </ul>
      <OutputsChoice designId={designId} revision={revision} />

      <details>
        <summary>Source code</summary>
        {Object.entries(revision.spec?.sources ?? {}).map(([path, text]) => (
          <details key={path} className="source-file">
            <summary className="muted">{path}</summary>
            <pre className="code">{text}</pre>
          </details>
        ))}
      </details>
    </div>
  );
}
