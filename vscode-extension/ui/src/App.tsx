import { DesignView } from "./components/DesignView";
import { FileView } from "./components/FileView";
import { InspectorView } from "./components/InspectorView";
import { view } from "./lib/host";

/** hitlcad in a VS Code webview: a design's 3D editor, its Design view or a model file, as the extension says. */
export function App() {
  return (
    <div className="app">
      {view.name === "design" ? (
        <DesignView id={view.id} />
      ) : view.name === "inspector" ? (
        <InspectorView id={view.id} />
      ) : (
        <FileView path={view.path} />
      )}
    </div>
  );
}
