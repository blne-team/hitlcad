import { resolveInstances, type Shape, type Shapes } from "three-cad-viewer";
import { json } from "../api/client";

/**
 * A 3D view as the server writes it (`geometry.cadview.Scene`): three-cad-viewer's instanced format, whose
 * instances name part view files beside it rather than carry their buffers.
 */
interface SceneFile {
  instances: { path: string; digest: string; shape: number }[];
  shapes: Shapes;
}

interface ViewArray {
  dtype: "float32" | "uint32";
  offset: number;
  length: number;
}

/** What the panel keeps of part views it has read: about this many bytes, the least recently drawn let go first. */
export const VIEW_CACHE_BYTES = 256 * 1024 * 1024;

/**
 * A part view file (`geometry.cadview.PartView`) as the viewer's shapes, its arrays read where they lie: a
 * little-endian u32, that many bytes of JSON header, then float32 and uint32 arrays at 4-byte offsets from it.
 */
export function readPartView(buffer: ArrayBuffer): Shape[] {
  const size = new DataView(buffer).getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, size))) as { arrays: Record<string, ViewArray>[] };
  const start = 4 + size;
  return header.arrays.map(
    (arrays) =>
      Object.fromEntries(
        Object.entries(arrays).map(([key, a]) => [
          key,
          a.dtype === "float32" ? new Float32Array(buffer, start + a.offset, a.length) : new Uint32Array(buffer, start + a.offset, a.length),
        ]),
      ) as unknown as Shape,
  );
}

/** Part views by digest: one fetch for a part whatever the revision or panel that draws it. */
class PartViews {
  private readonly views = new Map<string, Promise<Shape[]>>();
  private readonly sizes = new Map<string, number>();
  private bytes = 0;

  constructor(private readonly budget: number) {}

  get(digest: string, url: string): Promise<Shape[]> {
    const found = this.views.get(digest);
    if (found) {
      this.views.delete(digest); // to the back: drawn most recently
      this.views.set(digest, found);
      return found;
    }
    const loading = fetch(url)
      .then((res) => (res.ok ? res.arrayBuffer() : json<never>(res)))
      .then((buffer) => {
        this.sizes.set(digest, buffer.byteLength);
        this.bytes += buffer.byteLength;
        this.evict(digest);
        return readPartView(buffer);
      });
    loading.catch(() => this.forget(digest)); // a failed read is not kept: the next asks again
    this.views.set(digest, loading);
    return loading;
  }

  private evict(keep: string): void {
    for (const digest of this.views.keys()) {
      if (this.bytes <= this.budget) return;
      if (digest !== keep) this.forget(digest);
    }
  }

  private forget(digest: string): void {
    this.views.delete(digest);
    this.bytes -= this.sizes.get(digest) ?? 0;
    this.sizes.delete(digest);
  }
}

const partViews = new PartViews(VIEW_CACHE_BYTES);

/** A scene and its part views, as the shapes the viewer draws: each instance's view file resolved from the scene's own URL. */
export async function loadScene(url: string): Promise<Shapes> {
  const res = await fetch(url, { cache: "no-cache" }); // a revision being built writes it again
  const scene = await json<SceneFile>(res);
  const base = res.url || url; // where a redirect led: the part views lie beside the scene
  const drawn = await Promise.all(
    scene.instances.map(async ({ path, digest, shape }) => {
      const found = (await partViews.get(digest, new URL(path, base).href))[shape];
      if (!found) throw new Error(`${path} has no shape ${shape}`);
      return { ...found };
    }),
  );
  return resolveInstances(scene.shapes, drawn);
}
