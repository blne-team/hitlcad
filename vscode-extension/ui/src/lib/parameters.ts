import type { BuildReport, Revision } from "../api/types";

type BuildSpec = NonNullable<Revision["spec"]>;
type Reads = BuildReport["assembly_reads"];

/** Recorded inputs, including interface dependencies and component bindings, in the parent design's names.
 * Reads are per source file: siblings built by the same file can share the recorded parameter set. */
export function parametersForInstances(revision: Revision, instances: string[]): Set<string> {
  const collect = (report: BuildReport, spec: BuildSpec, paths: string[]): Set<string> => {
    const names = new Set<string>();
    const visited = new Set<string>();
    const read = (reads: Reads) => {
      reads.parameters.forEach((name) => names.add(name));
      reads.interfaces.forEach((name) => {
        if (visited.has(name)) return;
        visited.add(name);
        const value = report.interfaces.find((i) => i.name === name);
        if (value) read(value.reads);
      });
    };
    for (const path of paths) {
      const [head, ...tail] = path.split("/");
      const instance = report.instances.find((i) => i.path === head);
      if (!instance) continue;
      // Placement inputs are recorded for the assembly file, rather than for each instance.
      read(report.assembly_reads);
      if (instance.part) {
        const part = report.parts.find((p) => p.name === instance.part);
        if (part) read(part.reads);
      } else if (instance.component) {
        const child = report.components[instance.component];
        const childSpec = spec.components[instance.component];
        const use = spec.design.components[instance.component];
        if (!child || !childSpec || !use) continue;
        const needed = collect(child, childSpec, tail.length ? [tail.join("/")] : child.instances.map((i) => i.path));
        for (const name of needed) {
          const ref = use.bind[name];
          if (!ref) continue; // An unbound child parameter is edited in that component's own design.
          if (ref.startsWith("interfaces.")) read({ parameters: [], interfaces: [ref.slice("interfaces.".length)], modules: [] });
          else names.add(ref);
        }
      }
    }
    return names;
  };
  return revision.spec ? collect(revision.report, revision.spec, instances) : new Set();
}
