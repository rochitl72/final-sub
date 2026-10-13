import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export interface LatLng { lat: number; lng: number }

/** Leaflet + OpenStreetMap, locked to India. Click to drop a pin. */
export function IndiaMap({ pin, onPick, className }: { pin: LatLng | null; onPick: (p: LatLng) => void; className?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.CircleMarker | null>(null);
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, { zoomControl: true, attributionControl: true, maxBounds: [[5, 66], [37, 99]], minZoom: 4 })
      .setView([22.0, 79.0], 4);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 18 }).addTo(m);
    m.on('click', (e: L.LeafletMouseEvent) => pick.current({ lat: e.latlng.lat, lng: e.latlng.lng }));
    map.current = m;
    const t = setTimeout(() => m.invalidateSize(), 60);
    return () => { clearTimeout(t); m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    marker.current?.remove();
    marker.current = pin
      ? L.circleMarker([pin.lat, pin.lng], { radius: 9, color: '#2563eb', weight: 3, fillColor: '#60a5fa', fillOpacity: 0.9 }).addTo(m)
      : null;
  }, [pin?.lat, pin?.lng]);

  return <div ref={host} className={className} />;
}
