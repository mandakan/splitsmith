/**
 * Measured shell-chrome geometry.
 *
 * The shells used to hard-code the header height into sibling layout
 * (``h-[calc(100vh-86px)]`` on the sidebar, ``min-h-[calc(100vh-64px)]``
 * on the content row -- two different guesses for the same header). The
 * real header wraps taller than either once the shooter chip strip
 * renders, which pushed the sidebar's Jobs rail below the viewport.
 *
 * This hook measures the actual header and publishes it as the
 * ``--shell-header-h`` CSS custom property (set it on the shell root via
 * the returned style); layout reads the variable instead of guessing.
 */

import { useEffect, useRef, useState, type CSSProperties } from "react";

export function useShellHeaderHeight(fallbackPx = 86): {
  headerRef: React.RefObject<HTMLElement | null>;
  headerStyle: CSSProperties;
} {
  const headerRef = useRef<HTMLElement | null>(null);
  const [height, setHeight] = useState(fallbackPx);

  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    setHeight(el.offsetHeight);
    const ro = new ResizeObserver(() => setHeight(el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return {
    headerRef,
    headerStyle: { "--shell-header-h": `${height}px` } as CSSProperties,
  };
}
