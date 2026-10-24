import type { ParamValue, Result, Revision } from "../api/types";
import { diffLines, hunks } from "../lib/diff";
import { humanize } from "../lib/format";
import { MARK } from "./ReportPanel";

interface Props {
  revisions: Revision[];
  current: Revision | null;
  baseId: number | null;
  setBaseId: (id: number) => void;
}

const fmt = (v: ParamValue | undefined) => (v === undefined ? "—" : typeof v === "number" ? String(Number(v.toFixed(4))) : String(v));
const signed = (v: number, digits = 2) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
const key = (r: Result) => `${r.analysis}|${r.subject}|${r.about.join(",")}`;

/** What changed between an earlier revision and the one on screen: parameters, size, results, code. */
export function ChangesPanel({ revisions, current, baseId, setBaseId }: Props) {
  const base = revisions.find((r) => r.id === baseId) ?? null;
  if (!current || revisions.length < 2) return <p className="muted pad">Changes appear once there are two revisions.</p>;
  const options = revisions.filter((r) => r.id !== current.id);
  if (!base) return <p className="muted pad">Choose a revision to compare with.</p>;

  const specs = new Map([...base.spec.design.parameters, ...current.spec.design.parameters].map((p) => [p.name, p]));
  const [vb, vc] = [base.report.values, current.report.values];
  const params = [...new Set([...Object.keys(vb), ...Object.keys(vc)])]
    .filter((name) => vb[name] !== vc[name])
    .map((name) => ({ name, label: specs.get(name)?.label || humanize(name), old: vb[name], now: vc[name], unit: specs.get(name)?.unit ?? "" }));

  const mb = base.report.metrics;
  const mc = current.report.metrics;
  const before = new Map(base.report.results.map((r) => [key(r), r]));
  const moved = current.report.results.filter((r) => before.has(key(r)) && before.get(key(r))!.status !== r.status);
  const appeared = current.report.results.filter((r) => !before.has(key(r)) && r.status !== "pass");

  const files = [...new Set([...Object.keys(base.spec.sources), ...Object.keys(current.spec.sources)])].sort();
  const changed = files.filter((f) => base.spec.sources[f] !== current.spec.sources[f]);

  return (
    <div className="changes">
      <label className="changes-pick">
        <span>Compare revision {current.id} with</span>
        <select value={base.id} onChange={(e) => setBaseId(Number(e.target.value))}>
          {options.map((r) => (
            <option key={r.id} value={r.id}>
              revision {r.id}
              {r.spec.configuration ? ` (${r.spec.configuration})` : ""}
              {r.report.status === "failed" ? " (failed)" : ""}
            </option>
          ))}
        </select>
      </label>
      <p className="muted tiny">Compare draws revision {base.id} as a ghost over the model.</p>

      <h3>Parameters</h3>
      {params.length ? (
        <table className="metrics">
          <tbody>
            {params.map((p) => (
              <tr key={p.name}>
                <th title={p.name}>{p.label}</th>
                <td>
                  {fmt(p.old)} → <strong>{fmt(p.now)}</strong> {p.unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No parameter changes.</p>
      )}

      <h3>Size</h3>
      {mb && mc ? (
        <table className="metrics">
          <tbody>
            {(["X", "Y", "Z"] as const).map((axis, i) => (
              <tr key={axis}>
                <th>{axis}</th>
                <td>
                  {mb.size[i]!.toFixed(2)} → <strong>{mc.size[i]!.toFixed(2)}</strong> mm
                  {Math.abs(mc.size[i]! - mb.size[i]!) >= 0.005 && <span className="muted"> ({signed(mc.size[i]! - mb.size[i]!)})</span>}
                </td>
              </tr>
            ))}
            {base.report.mass && current.report.mass && (
              <tr>
                <th>Mass</th>
                <td>
                  {base.report.mass.mass_g.toFixed(1)} → <strong>{current.report.mass.mass_g.toFixed(1)}</strong> g
                </td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <p className="muted">One of the revisions did not build.</p>
      )}

      {(moved.length > 0 || appeared.length > 0) && (
        <>
          <h3>Results</h3>
          <ul className="checks">
            {moved.map((r, i) => (
              <li key={`m${i}`} className={r.status === "pass" ? "pass" : "miss sev-error"}>
                <span className="check-mark">{MARK[r.status]}</span>
                <span>
                  <span className="check-name">{r.subject}</span>
                  <span className="muted"> {before.get(key(r))!.status} → {r.status}: {r.detail}</span>
                </span>
              </li>
            ))}
            {appeared.map((r, i) => (
              <li key={`n${i}`} className="miss sev-error">
                <span className="check-mark">{MARK[r.status]}</span>
                <span>
                  <span className="check-name">{r.subject}</span>
                  <span className="muted"> new: {r.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>
        Code <span className="muted">{changed.length ? `${changed.length} file${changed.length > 1 ? "s" : ""} changed` : "unchanged"}</span>
      </h3>
      {changed.map((f) => {
        const diff = diffLines(base.spec.sources[f] ?? "", current.spec.sources[f] ?? "");
        const added = diff.filter((l) => l.kind === "+").length;
        const removed = diff.filter((l) => l.kind === "-").length;
        return (
          <details key={f} open={changed.length <= 3}>
            <summary>
              {f} <span className="muted">+{added} −{removed}</span>
            </summary>
            <pre className="code diff">
              {hunks(diff).map((l, i) =>
                l === null ? (
                  <span key={i} className="d-skip">
                    {"⋯\n"}
                  </span>
                ) : (
                  <span key={i} className={l.kind === "+" ? "d-add" : l.kind === "-" ? "d-del" : undefined}>
                    {`${l.kind} ${l.text}\n`}
                  </span>
                ),
              )}
            </pre>
          </details>
        );
      })}
    </div>
  );
}
