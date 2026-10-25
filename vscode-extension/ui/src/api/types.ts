// The server's API types, generated into schema.ts from its pydantic models (scripts/api-types.sh).
// All lengths are millimetres, Z up.

import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type Vec3 = [number, number, number];

export type ParamSpec = Schemas["ParamSpec"];
export type ParamValue = ParamSpec["value"];
export type DesignBrief = Schemas["DesignBrief"];
export type Revision = Schemas["Revision"];
export type BuildReport = Schemas["BuildReport"];
/** One finding of one analysis, naming the addresses it is about. */
export type Result = Schemas["Result"];
/** The results that share a cause, failing or unverified, with copies at other pattern indices folded in. */
export type Problem = Schemas["Problem"];
export type Finding = Schemas["Finding"];
export type Tally = Schemas["Tally"];
export type Capabilities = Schemas["Capabilities"];
export type AnalysisInfo = Schemas["AnalysisInfo"];
/** A click in a 3D view: the surface point, and on B-rep builds the instance's face, edge or vertex it hit. */
export type Pick = Schemas["Pick"];
export type SelectionItem = Schemas["SelectionItem"];
export type Pin = Schemas["Pin"];
export type DesignState = Schemas["DesignState"];

export type DesignEvent =
  paths["/api/designs/{design_id}/events"]["get"]["responses"][200]["content"]["application/json"][number];

/** A node or edge of the Structure view: its worst result, a failing warning counted apart. */
export type Status = Result["status"] | "warning" | "none";

/** The design graph for the Structure view (`/api/designs/{id}/structure`). */
export interface StructureGraph {
  nodes: { id: string; kind: "made" | "catalog" | "component"; label: string; role: string; count: number; status: Status }[];
  edges: { id: string; kind: "mate" | "relation" | "fastener"; type: string; source: string; target: string; status: Status }[];
  ground: string | null;
}

export interface Structure {
  overview: string;
  graph: StructureGraph;
}
