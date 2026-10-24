// The server's API types, generated into schema.ts from its pydantic models (scripts/api-types.sh).
// All lengths are millimetres, Z up.

import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type Vec3 = [number, number, number];

export type ParamSpec = Schemas["ParamSpec"];
export type ParamValue = ParamSpec["value"];
export type DesignBrief = Schemas["DesignBrief"];
export type Revision = Schemas["Revision"];
/** A click in a 3D view: the surface point, and on STEP models the face, edge or vertex it hit. */
export type Pick = Schemas["Pick"];
export type SelectionItem = Schemas["SelectionItem"];
export type Pin = Schemas["Pin"];
export type DesignState = Schemas["DesignState"];
export type Capabilities = Schemas["Capabilities"];

export type DesignEvent =
  paths["/api/designs/{design_id}/events"]["get"]["responses"][200]["content"]["application/json"][number];
