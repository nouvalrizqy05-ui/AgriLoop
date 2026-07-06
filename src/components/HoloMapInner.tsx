"use client";

import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Bypass Next.js image compilation issues by using unpkg CDN for Leaflet default icons
const customIcon = new L.Icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  tooltipAnchor: [16, -28],
  shadowSize: [41, 41]
});

interface HoloMapInnerProps {
  lat: number;
  lng: number;
  locationName: string;
}

export default function HoloMapInner({ lat, lng, locationName }: HoloMapInnerProps) {
  useEffect(() => {
    // Leaflet map container needs to know if container size changes, 
    // force a resize event slightly after mount just in case CSS flexbox shifted it
    setTimeout(() => {
      window.dispatchEvent(new Event("resize"));
    }, 100);
  }, []);

  return (
    <div className="relative group overflow-hidden rounded-xl border border-emerald-500/30 bg-slate-900/50 p-2 backdrop-blur-md shadow-[0_0_15px_rgba(16,185,129,0.2)] transition-all hover:shadow-[0_0_25px_rgba(16,185,129,0.4)]">
      {/* Hologram / Spaceship UI Overlays */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none z-10">
        <div className="absolute top-2 left-2 w-8 h-8 border-t-2 border-l-2 border-emerald-500 opacity-50" />
        <div className="absolute top-2 right-2 w-8 h-8 border-t-2 border-r-2 border-emerald-500 opacity-50" />
        <div className="absolute bottom-2 left-2 w-8 h-8 border-b-2 border-l-2 border-emerald-500 opacity-50" />
        <div className="absolute bottom-2 right-2 w-8 h-8 border-b-2 border-r-2 border-emerald-500 opacity-50" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(16,185,129,0)_0%,rgba(16,185,129,0.1)_100%)] pointer-events-none" />
      </div>

      <div 
        className="relative z-0 rounded-lg overflow-hidden h-64 w-full" 
        style={{ filter: "hue-rotate(180deg) invert(100%) brightness(80%) contrast(120%)" }}
      >
        <MapContainer center={[lat, lng]} zoom={13} scrollWheelZoom={false} style={{ height: "100%", width: "100%", zIndex: 1 }}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <Marker position={[lat, lng]} icon={customIcon}>
            <Popup>
              {locationName}
            </Popup>
          </Marker>
        </MapContainer>
      </div>

      {/* Futuristic HUD overlay texts */}
      <div className="absolute bottom-4 left-4 z-20 pointer-events-none flex flex-col gap-1">
        <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-widest animate-pulse">
          SYS.LOC.TRK // ACTIVE
        </span>
        <span className="text-xs font-mono text-emerald-300">
          LAT: {lat.toFixed(4)} LNG: {lng.toFixed(4)}
        </span>
      </div>
    </div>
  );
}
