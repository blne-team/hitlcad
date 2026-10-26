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
import type { CadMove } from "./motion";

/** A numbered marker drawn over the model: an agent pin or a selected item. */
export interface CadMarker {
  id: string;
  kind: "pin" | "selection";
  /** Where it is on its instance as built: drawn wherever the view has moved that instance to. */
  point: Vec3;
  instance: string | null;
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

/** A mate's motion at one position: what moves, each by its rigid motion from as built (`motion.movesAt`). */
export type CadMotion = CadMove[];

const TREE_WIDTH = 240;
const CLICK_SLOP_PX = 4; // a press that moves further is an orbit, not a click
const EASE = 0.25; // of the way to an explode's new spread, each frame
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
/** A box's eight corners. */
function corners([lo, hi]: [Vec3, Vec3]): Vec3[] {
  const out: Vec3[] = [];
  for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) out.push([x, y, z]);
  return out;
}

export const instanceOf = (viewerPath: string): string => viewerPath.replace(/^\/+/, "").split("/").slice(1).join("/");

interface ViewNode {
  id: string;
  exact?: boolean;
  parts?: ViewNode[];
}

/** Every node of a view's tree: the design, its assemblies and its shapes. */
function nodes(data: unknown): ViewNode[] {
  const out: ViewNode[] = [];
  const walk = (node: ViewNode) => {
    out.push(node);
    node.parts?.forEach(walk);
  };
  walk((data as { shapes: ViewNode }).shapes);
  return out;
}

/**
 * The ids of a view's exact shapes: B-rep parts, whose faces, edges and vertices are the part's own, numbered
 * as the server numbers them (it marks them `exact`). A click on one names its component; a click on any
 * other shape (a mesh) is a point.
 */
export const exactShapes = (data: unknown): Set<string> => new Set(nodes(data).filter((n) => n.exact).map((n) => n.id));

/** The ids of a view's shapes, the leaves of its tree: what an explode pulls apart. */
export const shapesOf = (data: unknown): string[] => nodes(data).filter((n) => !n.parts?.length).map((n) => n.id);

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
 * (clicks), `scene` with the three.js the viewer publishes as `window.THREE` (overlays), and
 * `nestedGroup` (parts moved by a motion or an explode).
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
  /** Markers drawn at a size on screen, not in the model: their unit is one CSS pixel (see `sizeMarkers`). */
  private readonly onScreen: Three.Object3D[] = [];
  private ghost: Three.Group | null = null;
  private readonly rootId: string;
  private readonly exact: Set<string>;
  /** Every group `place` has moved, as built. */
  private readonly built = new Map<Three.Object3D, { position: Three.Vector3; quaternion: Three.Quaternion }>();
  private marked: CadMarker[] = [];
  /** Each shape's way out from the model's middle, in its parent's frame: its offset at a spread of 1. */
  private readonly outward = new Map<Three.Object3D, Three.Vector3>();
  private motion: CadMotion | null = null;
  private exploded = false;
  /** How far an explode pulls the shapes apart: times each one's distance from the model's middle. */
  private spreadOut = 1;
  private spread = 0;
  private spreadTo = 0;
  private tween = 0;
  private press: { x: number; y: number } | null = null;
  private canvasSize: { width: number; height: number };

  constructor(
    private readonly container: HTMLElement,
    data: unknown,
    dark: boolean,
    private readonly onPick: (pick: Pick, additive: boolean) => void,
    private readonly onExplode: (on: boolean) => void,
  ) {
    this.exact = exactShapes(data);
    const ids = shapesOf(data);
    const shapes = decode(data);
    this.rootId = shapes.id;
    const bb = shapes.bb;
    this.bounds = bb ? { min: [bb.xmin, bb.ymin, bb.zmin], max: [bb.xmax, bb.ymax, bb.zmax] } : null;

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
      explodeTool: ids.length > 1, // one shape: nothing to pull apart
      zebraTool: true,
      zscaleTool: false,
      studioTool: false,
      externalMeasurementBackend: false,
      pinning: false,
    } as const;
    this.display = new Display(container, options);
    this.viewer = new Viewer(this.display, options, null);
    this.viewer.render(shapes, RENDER, VIEW);
    this.three = window.THREE as typeof Three; // set by the Viewer constructor
    this.markers = new this.three.Group();
    this.viewer.scene.add(this.markers);
    this.viewer.scene.onBeforeRender = (renderer, _scene, camera) => this.sizeMarkers(renderer, camera);
    this.explodeFrom(ids);
    // The toolbar's explode button (and its key, x) calls the viewer's setExplode, which only armed a clip to
    // play from a slider. Here the shapes come apart on the click and stay apart until the next.
    this.viewer.setExplode = (flag: boolean) => {
      this.exploded = flag;
      this.toward(flag ? this.spreadOut : 0);
      this.onExplode(flag);
    };

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
    this.marked = markers;
    this.drawMarkers();
    this.redraw();
  }

  private drawMarkers() {
    this.clear(this.markers);
    this.onScreen.length = 0;
    let pins = 0;
    let selected = 0;
    for (const marker of this.marked) {
      const m = this.whereShown(marker);
      this.markers.add(m.kind === "pin" ? this.pin(m, ++pins) : this.selection(m, ++selected));
    }
  }

  /** A marker where its instance is on screen, moved as the view moved the instance (a motion, an explode). */
  private whereShown(m: CadMarker): CadMarker {
    const moved = m.instance === null ? null : this.displaced(`${this.rootId}/${m.instance}`);
    if (!moved) return m;
    const T = this.three;
    const at = (p: Vec3) => new T.Vector3(...p).applyMatrix4(moved).toArray();
    const turned = m.normal && new T.Vector3(...m.normal).transformDirection(moved).toArray();
    const box = m.box && new T.Box3().setFromPoints(corners(m.box).map((c) => new T.Vector3(...c).applyMatrix4(moved)));
    return { ...m, point: at(m.point), normal: turned ?? null, box: box ? [box.min.toArray(), box.max.toArray()] : undefined };
  }

  /** How the view has moved a group, or the one it is in (a mate's motion, an explode), from where it was built,
   * in model space; null where it has not. */
  private displaced(key: string): Three.Matrix4 | null {
    const T = this.three;
    for (let g: Three.Object3D | null = this.viewer.nestedGroup.groups[key] ?? null; g; g = g.parent) {
      const base = this.built.get(g);
      if (!base || !g.parent) continue;
      const parent = g.parent.matrixWorld;
      const now = new T.Matrix4().compose(g.position, g.quaternion, g.scale);
      const then = new T.Matrix4().compose(base.position, base.quaternion, g.scale);
      return parent.clone().multiply(now).multiply(then.invert()).multiply(parent.clone().invert());
    }
    return null;
  }

  /**
   * Move instances by a mate's motion at one position (their viewer groups, `${root}/${instance}`), or put
   * everything back (`null`). The build places parts as assembled; this only previews the motion the build swept.
   */
  setMotion(motion: CadMotion | null): void {
    this.motion = motion;
    this.place();
  }

  /** How far the toolbar's explode pulls the shapes apart, times each one's distance from the model's middle. */
  setSpread(spread: number): void {
    this.spreadOut = spread;
    if (this.exploded) this.toward(spread);
  }

  /** Ease the explode's spread to `spread` over a few frames (0: as built). */
  private toward(spread: number) {
    this.spreadTo = spread;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      cancelAnimationFrame(this.tween);
      this.tween = 0;
      this.spread = spread;
      this.place();
    } else if (!this.tween) {
      this.tween = requestAnimationFrame(this.ease);
    }
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
    cancelAnimationFrame(this.tween);
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

  // ------------------------------------------------------------------ parts moved: a mate's motion, an explode

  /** Each shape's way out from the middle of the model as built: from its middle, as far again. */
  private explodeFrom(ids: string[]) {
    const T = this.three;
    const { groups, rootGroup } = this.viewer.nestedGroup;
    if (!rootGroup) return;
    this.viewer.scene.updateMatrixWorld(true);
    const middle = new T.Box3().setFromObject(rootGroup).getCenter(new T.Vector3());
    for (const id of ids) {
      const group = groups[id];
      const parent = group?.parent;
      const box = parent ? new T.Box3().setFromObject(group) : null;
      if (!parent || !box || box.isEmpty()) continue;
      const center = box.getCenter(new T.Vector3());
      const out = center.clone().multiplyScalar(2).sub(middle);
      this.outward.set(group, parent.worldToLocal(out).sub(parent.worldToLocal(center)));
    }
  }

  private readonly ease = () => {
    this.spread += (this.spreadTo - this.spread) * EASE;
    if (Math.abs(this.spreadTo - this.spread) < 0.002) this.spread = this.spreadTo;
    this.tween = this.spread === this.spreadTo ? 0 : requestAnimationFrame(this.ease);
    this.place();
  };

  /** Put every moved group back as built, then move it along the motion and out by the explode's spread. */
  private place() {
    const T = this.three;
    for (const [group, base] of this.built) {
      group.position.copy(base.position);
      group.quaternion.copy(base.quaternion);
    }
    const hold = (group: Three.Object3D) => {
      if (!this.built.has(group)) this.built.set(group, { position: group.position.clone(), quaternion: group.quaternion.clone() });
    };
    for (const move of this.motion ?? []) {
      const step = new T.Matrix4().fromArray([...move.matrix, 0, 0, 0, 1]).transpose(); // by rows, as the report has it
      for (const instance of move.instances) {
        const group = this.viewer.nestedGroup.groups[`${this.rootId}/${instance}`];
        if (!group) continue;
        hold(group);
        const placed = step.clone().multiply(new T.Matrix4().compose(group.position, group.quaternion, group.scale));
        placed.decompose(group.position, group.quaternion, new T.Vector3());
      }
    }
    if (this.spread !== 0) {
      for (const [group, out] of this.outward) {
        hold(group);
        group.position.addScaledVector(out, this.spread);
      }
    }
    this.viewer.scene.updateMatrixWorld(true);
    this.drawMarkers(); // markers go with their parts
    this.viewer.idPicker?.setDirty(); // the pick buffer shows the parts where they were drawn last
    this.redraw();
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
    const m = COMPONENT_PATH.exec(hit.info.path);
    const shape = hit.info.solidPath ?? m?.[1];
    const moved = shape ? this.displaced(shape.replace(/^\/+/, "")) : null; // a pick is where it is as built
    const point = (moved ? hit.point.clone().applyMatrix4(moved.invert()) : hit.point).toArray();
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

  /**
   * A group at `at` whose unit is one CSS pixel on screen, so what it holds keeps its size however far the
   * camera zooms, on a small model or a large one.
   */
  private onScreenAt(at: Three.Vector3): Three.Group {
    const group = new this.three.Group();
    group.position.copy(at);
    this.onScreen.push(group);
    return group;
  }

  /**
   * Size the on-screen markers for the frame about to be drawn: after three.js has placed the scene and before
   * it draws it, so they are never a frame behind the camera.
   */
  private sizeMarkers(renderer: Three.WebGLRenderer, camera: Three.Camera) {
    if (!this.onScreen.length) return;
    const height = renderer.getSize(new this.three.Vector2()).y;
    for (const group of this.onScreen) group.scale.setScalar(this.pixel(group.position, camera, height));
    this.markers.updateMatrixWorld(true);
  }

  /** The model's length of one CSS pixel at `at`, as `camera` sees it on a canvas `height` pixels high. */
  private pixel(at: Three.Vector3, camera: Three.Camera, height: number): number {
    const ortho = camera as Three.OrthographicCamera;
    if (ortho.isOrthographicCamera) return (ortho.top - ortho.bottom) / ortho.zoom / height;
    const persp = camera as Three.PerspectiveCamera;
    const depth = -at.clone().applyMatrix4(persp.matrixWorldInverse).z;
    return (2 * depth * Math.tan((persp.fov * Math.PI) / 360)) / persp.zoom / height;
  }

  /** A label `size` px across in an on-screen group; `center` is the point of it at its position (Sprite.center). */
  private sprite(map: Three.Texture, at: Three.Vector3, size: number, center: [number, number] = [0.5, 0.5]): Three.Sprite {
    const s = new this.three.Sprite(new this.three.SpriteMaterial({ map, depthTest: false, transparent: true }));
    s.position.copy(at);
    s.scale.set(size, size, 1);
    s.center.set(...center);
    s.renderOrder = 11;
    return s;
  }

  private pin(m: CadMarker, n: number): Three.Group {
    const T = this.three;
    const normal = new T.Vector3(...(m.normal ?? [0, 0, 1])).normalize();
    const head = normal.clone().multiplyScalar(28);
    const group = this.onScreenAt(new T.Vector3(...m.point));
    const dot = new T.Mesh(new T.SphereGeometry(4, 16, 12), new T.MeshBasicMaterial({ color: PIN_COLOR }));
    group.add(dot, new T.LineSegments(new T.BufferGeometry().setFromPoints([new T.Vector3(), head]), new T.LineBasicMaterial({ color: INK })));
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
    group.add(this.sprite(tex, head, 26));
    return group;
  }

  private selection(m: CadMarker, n: number): Three.Group {
    if (m.box) return this.partSelection(m.box, n);
    const T = this.three;
    const normal = new T.Vector3(...(m.normal ?? [0, 0, 1])).normalize();
    const group = this.onScreenAt(new T.Vector3(...m.point));
    const ring = new T.Mesh(
      new T.RingGeometry(8, 12, 40),
      new T.MeshBasicMaterial({ color: SELECT_COLOR, side: T.DoubleSide, depthTest: false, transparent: true, opacity: 0.95 }),
    );
    ring.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), normal);
    ring.renderOrder = 9;
    const dot = new T.Mesh(new T.SphereGeometry(2.5, 12, 8), new T.MeshBasicMaterial({ color: SELECT_COLOR, depthTest: false }));
    dot.renderOrder = 9;
    group.add(ring, dot, this.selectionTag(n, [-0.55, -0.55])); // up and to the right of the ring
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
    const tag = this.onScreenAt(new T.Vector3(...max));
    tag.add(this.selectionTag(n, [0, 0]));
    const group = new T.Group();
    group.add(lines, tag);
    return group;
  }

  /** A selection's number, 18 px across; `center` as for `sprite`. */
  private selectionTag(n: number, center: [number, number]): Three.Sprite {
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
    return this.sprite(tex, new this.three.Vector3(), 18, center);
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
