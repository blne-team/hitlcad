import type { ParamValue, Revision } from "../api/types";
import { diffLines, hunks } from "../lib/diff";
import { humanize } from "../lib/format";

interface Props {
  revisions: Revision[];
  current: Revision | null;
  baseId: number | null;
  setBaseId: (id: number) => void;
}

const fmt = (v: ParamValue | undefined) => (v === undefined ? "—" : typeof v === "number" ? String(Number(v.toFixed(4))) : String(v));
const signed = (v: number, digits = 2) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;

/** What changed between an earlier revision and the one on screen: parameters, size, checks, source. */
export function ChangesPanel({ revisions, current, baseId, setBaseId }: Props) {
  const base = revisions.find((r) => r.id === baseId) ?? null;
  if (!current || revisions.length < 2) return <p className="muted pad">Changes appear once there are two revisions.</p>;
  const options = revisions.filter((r) => r.id !== current.id);
  if (!base) return <p className="muted pad">Choose a revision to compare with.</p>;

  const before = new Map(base.spec.parameters.map((p) => [p.name, p]));
  const after = new Map(current.spec.parameters.map((p) => [p.name, p]));
  const params = [...new Set([...before.keys(), ...after.keys()])]
    .map((name) => ({ name, label: after.get(name)?.label || before.get(name)?.label || humanize(name), old: before.get(name)?.value, now: after.get(name)?.value, unit: (after.get(name) ?? before.get(name))!.unit }))
    .filter((p) => p.old !== p.now);

  const mb = base.report.metrics;
  const mc = current.report.metrics;
  const failing = (r: Revision) => new Set(r.report.checks.filter((c) => !c.passed).map((c) => c.name));
  const [fb, fc] = [failing(base), failing(current)];
  const newlyFailing = [...fc].filter((n) => !fb.has(n));
  const fixed = [...fb].filter((n) => !fc.has(n));

  const diff = diffLines(base.spec.source, current.spec.source);
  const added = diff.filter((l) => l.kind === "+").length;
  const removed = diff.filter((l) => l.kind === "-").length;

  return (
    <div className="changes">
      <label className="changes-pick">
        <span>Compare revision {current.id} with</span>
        <select value={base.id} onChange={(e) => setBaseId(Number(e.target.value))}>
          {options.map((r) => (
            <option key={r.id} value={r.id}>
              revision {r.id}
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
            {mb.volume !== null && mc.volume !== null && (
              <tr>
                <th>Volume</th>
                <td>
                  {mb.volume.toFixed(0)} → <strong>{mc.volume.toFixed(0)}</strong> mm³
                  <span className="muted"> ({signed(((mc.volume - mb.volume) / (mb.volume || 1)) * 100, 1)}%)</span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <p className="muted">One of the revisions did not build.</p>
      )}

      {(newlyFailing.length > 0 || fixed.length > 0) && (
        <>
          <h3>Checks</h3>
          <ul className="checks">
            {newlyFailing.map((n) => (
              <li key={n} className="miss sev-error">
                <span className="check-mark">!</span>
                <span>{n.replace(/_/g, " ")} now fails</span>
              </li>
            ))}
            {fixed.map((n) => (
              <li key={n} className="pass">
                <span className="check-mark">✓</span>
                <span>{n.replace(/_/g, " ")} fixed</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>
        model.py <span className="muted">{added || removed ? `+${added} −${removed} lines` : "unchanged"}</span>
      </h3>
      {(added > 0 || removed > 0) && (
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
      )}
    </div>
  );
}
