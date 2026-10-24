import type * as Three from "three";
import {
  decodeInstancedFormat,
  Display,
  isInstancedFormat,
  type RenderOptions,
  type Shapes,
  Viewer,
  type ViewerOptions,
} from "three-cad-viewer";
import "three-cad-viewer/css";
import type { Pick, Vec3 } from "../api/types";

/** A numbered marker drawn over the model: an agent pin or a selected item. */
export interface CadMarker {
  id: string;
  kind: "pin" | "selection";
  point: Vec3;
  normal: Vec3 | null;
  label: string;
  active?: boolean;
  /** A selected whole part: its bounding box, drawn instead of a ring at the point. */
  box?: [Vec3, Vec3];
}

export interface CadBounds {
  min: Vec3;
  max: Vec3;
}

/** Instances moved along a mate's free motion: turned `amount` degrees about the axis, or slid `amount` mm along it. */
export interface CadMotion {
  instances: string[];
  origin: Vec3;
  axis: Vec3;
  kind: "angle" | "travel";
  amount: number;
}

const TREE_WIDTH = 240;
const CLICK_SLOP_PX = 4; // a press that moves further is an orbit, not a click
const COMPONENT_PATH = /^(.*)\/(faces|edges|vertices)\/\2_(\d+)$/;
const TOPO = { faces: "face", edges: "edge", vertices: "vertex" } as const;
const PIN_COLOR = "#F5C400";
const INK = "#1B2127";
const SELECT_COLOR = "#2f6df6";
const GHOST_COLOR = "#4c6ef5";

const RENDER: RenderOptions = { edgeColor: 0x707070, ambientIntensity: 1, directIntensity: 1.1, metalness: 0.3, roughness: 0.65, defaultOpacity: 0.5, normalLen: 0 };
// The axes and the grid at the design's origin (0, 0, 0), which its parts are placed from, not at the middle of
// its bounding box: three-cad-viewer's default moves them with the model's extent.
const VIEW: ViewerOptions = { up: "Z", control: "orbit", ortho: true, axes: false, axes0: true, grid: [true, false, false], collapse: 1 };

/** The instance a viewer path names: "/Gearbox/tilt/planet[2]" -> "tilt/planet[2]" (the root is the design). */
export const instanceOf = (viewerPath: string): string => viewerPath.replace(/^\/+/, "").split("/").slice(1).join("/");

/**
 * The ids of a view's exact shapes: B-rep parts, whose faces, edges and vertices are the part's own, numbered
 * as the server numbers them (it marks them `exact`). A click on one names its component; a click on any
 * other shape (a mesh) is a point.
 */
export function exactShapes(data: unknown): Set<string> {
  interface Node {
    id: string;
    exact?: boolean;
    parts?: Node[];
  }
  const out = new Set<string>();
  const walk = (node: Node) => {
    if (node.exact) out.add(node.id);
    node.parts?.forEach(walk);
  };
  walk((data as { shapes: Node }).shapes);
  return out;
}

const decode = (data: unknown): Shapes => {
  if (!isInstancedFormat(data)) throw new Error("the server sent a model the viewer cannot read");
  return decodeInstancedFormat(data);
};
const flat = (a: ArrayLike<number> | number[][]): ArrayLike<number> => (Array.isArray(a) ? (a as (number | number[])[]).flat() : a);

/**
 * three-cad-viewer in a container: sized to it, themed with the page, reporting clicks, with
 * hitlcad's own markers and compare ghost drawn into its scene.
 *
 * The one place that reaches past the viewer's documented API: `idPicker` and the shape filter
 * (clicks), and `scene` with the three.js the viewer publishes as `window.THREE` (overlays).
 * three-cad-viewer is pinned; re-check `pick` and the overlay code when upgrading it.
 */
export class CadView {
  readonly bounds: CadBounds | null;
  private readonly display: Display;
  private readonly viewer: Viewer;
  private readonly three: typeof Three;
  private readonly observer: ResizeObserver;
  private readonly canvas: HTMLCanvasElement;
  private readonly markers: Three.Group;
  private readonly extent: number;
  private ghost: Three.Group | null = null;
  private readonly rootId: string;
  private readonly exact: Set<string>;
  private readonly moved = new Map<Three.Object3D, { position: Three.Vector3; quaternion: Three.Quaternion }>();
  private press: { x: number; y: number } | null = null;
  private canvasSize: { width: number; height: number };

  constructor(
    private readonly container: HTMLElement,
    data: unknown,
    dark: boolean,
    private readonly onPick: (pick: Pick, additive: boolean) => void,
  ) {
    this.exact = exactShapes(data);
    const shapes = decode(data);
    this.rootId = shapes.id;
    const bb = shapes.bb;
    this.bounds = bb ? { min: [bb.xmin, bb.ymin, bb.zmin], max: [bb.xmax, bb.ymax, bb.zmax] } : null;
    this.extent = bb ? Math.hypot(bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin) : 100;

    // Start at the container's size (at least 1 px: the viewer ignores 0); `fit` then subtracts the viewer's own chrome.
    this.canvasSize = { width: Math.max(container.clientWidth, 1), height: Math.max(container.clientHeight, 1) };
    // Studio mode downloads HDR environments from polyhaven.org; hitlcad stays offline.
    const options = {
      cadWidth: this.canvasSize.width,
      height: this.canvasSize.height,
      treeWidth: TREE_WIDTH,
      theme: dark ? "dark" : "light",
      glass: true,
      tools: true,
      measureTools: true,
      selectTool: false, // hitlcad's own selection, which reaches the agent, replaces the viewer's
      explodeTool: true,
      zebraTool: true,
      zscaleTool: false,
      studioTool: false,
      externalMeasurementBackend: false,
      pinning: false,
    } as const;
    this.display = new Display(container, options);
    this.viewer = new Viewer(this.display, options, null);
    this.viewer.render(shapes, RENDER, VIEW);
    // three.js finds an animated node by a track name it parses, and a part named with brackets or dots
    // (motor_screw[0], a STEP file's Part.1) does not parse: the viewer's explode threw. Name its tracks by uuid.
    (this.viewer.animation as unknown as { _prepareSelector: (id: string) => string })._prepareSelector = (id) =>
      this.viewer.nestedGroup.groups[id]!.uuid;
    this.three = window.THREE as typeof Three; // set by the Viewer constructor
    this.markers = new this.three.Group();
    this.viewer.scene.add(this.markers);

    this.canvas = this.viewer.renderer.domElement;
    this.canvas.addEventListener("pointerdown", this.onDown);
    this.canvas.addEventListener("pointerup", this.onUp);
    this.observer = new ResizeObserver(() => this.fit());
    this.observer.observe(container);
    this.fit();
  }

  setDark(dark: boolean): void {
    this.display.setTheme(dark ? "dark" : "light");
  }

  /** Turn off the viewer's measure tool if one is on, as its toolbar button does, so clicks pick again. */
  stopTool(): void {
    const tool = this.viewer.state.get("activeTool");
    if (tool) this.container.querySelector<HTMLInputElement>(`.tcv_button_${tool}`)?.click();
  }

  /** Replace the numbered markers (pins and selected items). */
  setMarkers(markers: CadMarker[]): void {
    this.clear(this.markers);
    let pins = 0;
    let selected = 0;
    for (const m of markers) {
      this.markers.add(m.kind === "pin" ? this.pin(m, ++pins) : this.selection(m, ++selected));
    }
    this.redraw();
  }

  /**
   * Move instances along a mate's free motion (their viewer groups, `${root}/${instance}`), or put everything
   * back (`null`). The build places parts as assembled; this only previews the motion the build swept.
   */
  setMotion(motion: CadMotion | null): void {
    for (const [group, base] of this.moved) {
      group.position.copy(base.position);
      group.quaternion.copy(base.quaternion);
    }
    this.moved.clear();
    if (motion && motion.amount !== 0) {
      const T = this.three;
      const axis = new T.Vector3(...motion.axis).normalize();
      const origin = new T.Vector3(...motion.origin);
      const step =
        motion.kind === "angle"
          ? new T.Matrix4()
              .makeTranslation(origin.x, origin.y, origin.z)
              .multiply(new T.Matrix4().makeRotationAxis(axis, (motion.amount * Math.PI) / 180))
              .multiply(new T.Matrix4().makeTranslation(-origin.x, -origin.y, -origin.z))
          : new T.Matrix4().makeTranslation(axis.x * motion.amount, axis.y * motion.amount, axis.z * motion.amount);
      for (const instance of motion.instances) {
        const group = this.viewer.nestedGroup.groups[`${this.rootId}/${instance}`];
        if (!group) continue;
        this.moved.set(group, { position: group.position.clone(), quaternion: group.quaternion.clone() });
        const placed = step.clone().multiply(new T.Matrix4().compose(group.position, group.quaternion, group.scale));
        placed.decompose(group.position, group.quaternion, new T.Vector3());
      }
    }
    this.redraw();
  }

  /** Show an earlier revision's model as an X-ray ghost, or remove it (`null`). */
  setGhost(data: unknown): void {
    if (this.ghost) {
      this.clear(this.ghost);
      this.viewer.scene.remove(this.ghost);
      this.ghost = null;
    }
    if (data !== null) {
      this.ghost = this.ghostOf(decode(data));
      this.viewer.scene.add(this.ghost);
    }
    this.redraw();
  }

  dispose(): void {
    this.observer.disconnect();
    this.canvas.removeEventListener("pointerdown", this.onDown);
    this.canvas.removeEventListener("pointerup", this.onUp);
    this.clear(this.markers);
    if (this.ghost) this.clear(this.ghost);
    this.viewer.dispose();
    this.display.dispose();
    this.container.replaceChildren();
  }

  /**
   * Size the canvas so the whole viewer fits the container. The viewer adds chrome around the
   * canvas (margin, borders, and its toolbar above it), so the canvas gets the container minus
   * that chrome, measured rather than assumed. Skipped while the container is hidden.
   */
  private fit() {
    const stage = this.container.getBoundingClientRect();
    const root = this.container.firstElementChild?.getBoundingClientRect();
    if (!root || stage.width < 1 || stage.height < 1) return;
    const chromeW = root.right - stage.left - this.canvasSize.width;
    const chromeH = root.bottom - stage.top - this.canvasSize.height;
    const width = Math.max(1, Math.floor(stage.width - chromeW));
    const height = Math.max(1, Math.floor(stage.height - chromeH));
    if (width === this.canvasSize.width && height === this.canvasSize.height) return;
    this.canvasSize = { width, height };
    this.viewer.resizeCadView(width, TREE_WIDTH, height, true);
  }

  private redraw() {
    this.viewer.update(true, false);
  }

  private readonly onDown = (e: PointerEvent) => {
    this.press = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
  };

  private readonly onUp = (e: PointerEvent) => {
    const press = this.press;
    this.press = null;
    if (!press || Math.hypot(e.clientX - press.x, e.clientY - press.y) > CLICK_SLOP_PX) return;
    if (this.viewer.state.get("activeTool")) return; // a measure tool's click: not a selection or a pin
    const pick = this.pick(e.clientX, e.clientY);
    if (pick) this.onPick(pick, e.shiftKey);
  };

  /**
   * Resolve a click with the viewer's GPU id picker, honouring its All/Vertex/Edge/Face/Solid filter. On an
   * exact shape the pick names its component; under Solid the picker hits a face and names the part it
   * belongs to (`solidPath`): the pick is that part. On any other shape it is a point.
   */
  private pick(clientX: number, clientY: number): Pick | null {
    const rect = this.canvas.getBoundingClientRect();
    const filter = this.exact.size ? this.display.shapeFilterDropDownMenu.currentFilter.filter((f) => f !== null) : ["face" as const];
    const hit = this.viewer.idPicker?.pickAt(clientX - rect.left, clientY - rect.top, filter.length ? { topoFilter: filter } : {});
    if (!hit?.point) return null;
    const point = hit.point.toArray();
    const m = COMPONENT_PATH.exec(hit.info.path);
    const shape = hit.info.solidPath ?? m?.[1];
    if (!shape || !this.exact.has(shape)) return { point, normal: null, component: null };
    if (filter.includes("solid")) return { point, normal: null, component: { instance: instanceOf(shape), topo: "solid", index: 0 } };
    if (!m) return null;
    return { point, normal: null, component: { instance: instanceOf(m[1]!), topo: TOPO[m[2] as keyof typeof TOPO], index: Number(m[3]) } };
  }

  // ------------------------------------------------------------------ overlays (the viewer's three.js)

  private clear(group: Three.Group) {
    group.traverse((o) => {
      const { geometry, material } = o as Three.Mesh;
      geometry?.dispose();
      for (const m of Array.isArray(material) ? material : material ? [material] : []) {
        (m as Three.SpriteMaterial).map?.dispose();
        m.dispose();
      }
    });
    group.clear();
  }

  private label(text: string, draw: (ctx: CanvasRenderingContext2D, size: number) => void, size: number): Three.CanvasTexture {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const ctx = c.getContext("2d")!;
    draw(ctx, size);
    ctx.font = `600 ${Math.round(size * 0.5)}px 'Barlow Semi Condensed', 'Barlow', sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, size / 2, size * 0.53);
    const tex = new this.three.CanvasTexture(c);
    tex.colorSpace = this.three.SRGBColorSpace;
    return tex;
  }

  /** A label `size` mm across: the viewer's camera is orthographic, so sprites are sized in model units. */
  private sprite(map: Three.Texture, at: Three.Vector3, size: number): Three.Sprite {
    const s = new this.three.Sprite(new this.three.SpriteMaterial({ map, depthTest: false, transparent: true }));
    s.position.copy(at);
    s.scale.set(size, size, 1);
    s.renderOrder = 11;
    return s;
  }

  private pin(m: CadMarker, n: number): Three.Group {
    const T = this.three;
    const radius = Math.max(this.extent * 0.006, 0.2);
    const normal = new T.Vector3(...(m.normal ?? [0, 0, 1])).normalize();
    const tip = new T.Vector3(...m.point);
    const head = tip.clone().addScaledVector(normal, radius * 7);
    const group = new T.Group();
    const dot = new T.Mesh(new T.SphereGeometry(radius, 16, 12), new T.MeshBasicMaterial({ color: PIN_COLOR }));
    dot.position.copy(tip);
    group.add(dot, new T.LineSegments(new T.BufferGeometry().setFromPoints([tip, head]), new T.LineBasicMaterial({ color: INK })));
    const tex = this.label(
      String(n),
      (ctx, s) => {
        ctx.beginPath();
        ctx.arc(s / 2, s / 2, s * 0.42, 0, Math.PI * 2);
        ctx.fillStyle = PIN_COLOR;
        ctx.fill();
        ctx.lineWidth = m.active ? 9 : 6;
        ctx.strokeStyle = INK;
        ctx.stroke();
        ctx.fillStyle = INK;
      },
      96,
    );
    group.add(this.sprite(tex, head, radius * 8));
    return group;
  }

  private selection(m: CadMarker, n: number): Three.Group {
    if (m.box) return this.partSelection(m.box, n);
    const T = this.three;
    const radius = Math.max(this.extent * 0.018, 0.4);
    const normal = new T.Vector3(...(m.normal ?? [0, 0, 1])).normalize();
    const point = new T.Vector3(...m.point);
    const center = point.clone().addScaledVector(normal, radius * 0.15); // off the surface: no z-fighting
    const ring = new T.Mesh(
      new T.RingGeometry(radius * 0.9, radius * 1.35, 40),
      new T.MeshBasicMaterial({ color: SELECT_COLOR, side: T.DoubleSide, depthTest: false, transparent: true, opacity: 0.95 }),
    );
    ring.position.copy(center);
    ring.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), normal);
    ring.renderOrder = 9;
    const dot = new T.Mesh(new T.SphereGeometry(radius * 0.22, 12, 8), new T.MeshBasicMaterial({ color: SELECT_COLOR, depthTest: false }));
    dot.position.copy(center);
    dot.renderOrder = 9;
    const tag = point.clone().addScaledVector(normal, radius * 1.4).add(new T.Vector3(0, 0, radius * 2.6)); // beside the ring
    const group = new T.Group();
    group.add(ring, dot, this.selectionTag(n, tag, radius * 1.8));
    return group;
  }

  /** A selected whole part: its bounding box, drawn on top so it shows through other parts, numbered at a top corner. */
  private partSelection([min, max]: [Vec3, Vec3], n: number): Three.Group {
    const T = this.three;
    const box = new T.Box3(new T.Vector3(...min), new T.Vector3(...max));
    const lines = new T.LineSegments(
      new T.EdgesGeometry(new T.BoxGeometry(...box.getSize(new T.Vector3()).toArray())),
      new T.LineBasicMaterial({ color: SELECT_COLOR, depthTest: false, transparent: true }),
    );
    lines.position.copy(box.getCenter(new T.Vector3()));
    lines.renderOrder = 9;
    const size = Math.max(this.extent * 0.018, 0.4) * 1.8;
    const group = new T.Group();
    group.add(lines, this.selectionTag(n, new T.Vector3(max[0], max[1], max[2]).add(new T.Vector3(0, 0, size)), size));
    return group;
  }

  private selectionTag(n: number, at: Three.Vector3, size: number): Three.Sprite {
    const tex = this.label(
      String(n),
      (ctx, s) => {
        ctx.fillStyle = SELECT_COLOR;
        ctx.beginPath();
        ctx.roundRect(s * 0.1, s * 0.1, s * 0.8, s * 0.8, s * 0.15);
        ctx.fill();
        ctx.fillStyle = "#ffffff";
      },
      64,
    );
    return this.sprite(tex, at, size);
  }

  /** The faces and true edges of `shapes`, drawn on top like an X-ray so the old shape shows inside the new one. */
  private ghostOf(shapes: Shapes): Three.Group {
    const T = this.three;
    const face = new T.MeshBasicMaterial({ color: GHOST_COLOR, transparent: true, opacity: 0.14, depthWrite: false, depthTest: false });
    const line = new T.LineBasicMaterial({ color: GHOST_COLOR, transparent: true, opacity: 0.85, depthTest: false });
    const build = (node: Shapes): Three.Object3D => {
      const group = new T.Group();
      if (node.loc) {
        group.position.set(...node.loc[0]);
        group.quaternion.set(...node.loc[1]);
      }
      for (const part of node.parts ?? []) group.add(build(part));
      if (node.type === "shapes" && node.shape) {
        const g = new T.BufferGeometry();
        g.setAttribute("position", new T.Float32BufferAttribute(flat(node.shape.vertices), 3));
        g.setIndex(Array.from(flat(node.shape.triangles)));
        const mesh = new T.Mesh(g, face.clone());
        mesh.renderOrder = 5;
        const edges = new T.BufferGeometry();
        edges.setAttribute("position", new T.Float32BufferAttribute(flat(node.shape.edges), 3));
        const lines = new T.LineSegments(edges, line.clone());
        lines.renderOrder = 6;
        group.add(mesh, lines);
      }
      return group;
    };
    const ghost = new T.Group();
    ghost.add(build(shapes));
    face.dispose();
    line.dispose();
    return ghost;
  }
}
