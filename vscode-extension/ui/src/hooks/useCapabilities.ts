import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Capabilities } from "../api/types";

let asked: Promise<Capabilities> | null = null;

/** What the server can do (its analyses, in the order they run), asked once per page; null until it answers. */
export function useCapabilities(): Capabilities | null {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  useEffect(() => {
    let live = true;
    (asked ??= api.capabilities()).then(
      (c) => live && setCapabilities(c),
      () => (asked = null), // asked again by the next panel that shows
    );
    return () => {
      live = false;
    };
  }, []);
  return capabilities;
}
