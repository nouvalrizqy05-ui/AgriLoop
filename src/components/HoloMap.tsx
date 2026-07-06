"use client";

import dynamic from "next/dynamic";
import { useState, useEffect } from "react";

// Dynamically import the Inner map component and disable SSR
// This guarantees Leaflet is only evaluated inside the browser
const HoloMapInner = dynamic(() => import("./HoloMapInner"), {
  ssr: false,
  loading: () => <div className="h-64 w-full bg-slate-900 rounded-lg animate-pulse border border-emerald-500/30 flex items-center justify-center text-emerald-500/50 font-mono text-xs">INITIALIZING SENSORS...</div>,
});

interface HoloMapProps {
  lat: number;
  lng: number;
  locationName: string;
}

export default function HoloMap(props: HoloMapProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <div className="h-64 w-full bg-slate-900 rounded-lg animate-pulse border border-emerald-500/30 flex items-center justify-center text-emerald-500/50 font-mono text-xs">BOOTING HUD...</div>;
  }

  return <HoloMapInner {...props} />;
}
