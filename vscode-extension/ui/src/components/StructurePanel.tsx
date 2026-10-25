import dagre from "@dagrejs/dagre";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Revision, Structure, StructureGraph } from "../api/types";

interface Props {
  designId: string;
  revision: Revision | null;
  configuration: string | null;
  /** The latest revision's id: the graph is refetched when a build lands. */
  latest: number | null;
  /** A part was selected (the design's selection, as a click in the 3D view makes it). */
  onSelected: () => void;
}

const NODE_W = 150;
const NODE_H = 46;
const KIND = { made: "made part", catalog: "catalog part", component: "component" } as const;

interface Layout {
  width: number;
  height: number;
  nodes: (StructureGraph["nodes"][number] & { x: number; y: number })[];
  edges: (StructureGraph["edges"][number] & { points: { x: number; y: number }[] })[];
}

function layout(graph: StructureGraph): Layout {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({ rankdir: "LR", nodesep: 22, ranksep: 70, marginx: 12, marginy: 12 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  const drawn = graph.edges.filter((e) => e.source !== e.target && g.hasNode(e.source) && g.hasNode(e.target));
  for (const e of drawn) g.setEdge(e.source, e.target, { width: 40, height: 14 }, e.id);
  dagre.layout(g);
  const box = g.graph();
  return {
    width: box.width ?? 0,
    height: box.height ?? 0,
    nodes: graph.nodes.map((n) => ({ ...n, x: g.node(n.id).x, y: g.node(n.id).y })),
    edges: drawn.map((e) => ({ ...e, points: g.edge({ v: e.source, w: e.target, name: e.id }).points })),
  };
}

/** The design's structure: parts and components, and the mates, relations and fastener sets between them. */
export function StructurePanel({ designId, revision, configuration, latest, onSelected }: Props) {
  const [data, setData] = useState<Structure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .structure(designId, configuration)
      .then((s) => !cancelled && (setData(s), setError(null)))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [designId, configuration, latest]);

  const drawn = useMemo(() => (data ? layout(data.graph) : null), [data]);
  if (error) return <p className="notice fail">{error}</p>;
  if (!data || !drawn) return <p className="muted pad">Reading the structure…</p>;
  if (!data.graph.nodes.length) return <p className="muted pad">No parts declared yet: your agent declares them in design.json.</p>;

  const select = async (id: string) => {
    const inst = revision?.report.instances.find((i) => i.part === id);
    if (!revision || !inst) return;
    const point: [number, number, number] = [inst.transform[0]![3]!, inst.transform[1]![3]!, inst.transform[2]![3]!];
    setSelectError(null);
    try {
      await api.setSelection(designId, revision.id, [{ point, normal: null, component: { instance: inst.path, topo: "solid", index: 0 } }]);
      onSelected();
    } catch (e) {
      setSelectError(e instanceof Error ? e.message : String(e));
    }
  };
  const failing = data.graph.edges.filter((e) => e.status === "fail");

  return (
    <div className="structure">
      <p className="muted">
        {data.graph.nodes.length} parts and components, {data.graph.edges.length} joints
        {failing.length ? <strong className="fail">, {failing.length} failing</strong> : ""}. Click a part to select it.
      </p>
      <div className="structure-canvas">
        <svg width={drawn.width} height={drawn.height} role="img" aria-label="Structure of the design">
          <defs>
            <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L8,4 L0,8 z" className="edge-head" />
            </marker>
          </defs>
          {drawn.edges.map((e) => {
            const d = e.points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
            const mid = e.points[Math.floor(e.points.length / 2)]!;
            return (
              <g key={e.id} className={`edge edge-${e.kind} st-${e.status}`}>
                <title>{`${e.kind} ${e.id}: ${e.status}`}</title>
                <path d={d} markerEnd={e.kind === "fastener" ? undefined : "url(#arrow)"} />
                <text x={mid.x} y={mid.y - 4} textAnchor="middle">
                  {e.type}
                </text>
              </g>
            );
          })}
          {drawn.nodes.map((n) => (
            <g key={n.id} className={`node node-${n.kind} st-${n.status}${n.id === data.graph.ground ? " ground" : ""}`}
              transform={`translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})`} onClick={() => void select(n.id)}>
              <title>{`${n.label}: ${n.role} (${KIND[n.kind]})`}</title>
              <rect width={NODE_W} height={NODE_H} rx={6} />
              <text x={10} y={19} className="node-label">
                {n.label}
                {n.count > 1 ? ` ×${n.count}` : ""}
              </text>
              <text x={10} y={35} className="node-role">
                {n.id === data.graph.ground ? "ground · " : ""}
                {KIND[n.kind]}
              </text>
            </g>
          ))}
        </svg>
      </div>
      {selectError && <p className="notice fail">{selectError}</p>}
      <details>
        <summary>What your agent reads</summary>
        <pre className="code">{data.overview}</pre>
      </details>
    </div>
  );
}
