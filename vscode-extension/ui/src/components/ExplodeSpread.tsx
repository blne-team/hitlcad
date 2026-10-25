/** How far the toolbar's explode pulls the shapes apart, over the viewer's toolbar while it is on. */
export function ExplodeSpread({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <label className="explode-spread">
      <span>Spread</span>
      <input type="range" aria-label="How far apart" min={0} max={3} step={0.05} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}
