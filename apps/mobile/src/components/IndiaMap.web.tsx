/** Web map for the PWA: Leaflet + OpenStreetMap tiles, same props as the native IndiaMap. */
import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { IndiaMapProps } from './IndiaMap';

export type { IndiaMapProps };

export function IndiaMap({ style, initialRegion, pin, onPress }: IndiaMapProps) {
  const host = useRef<any>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.CircleMarker | null>(null);
  const press = useRef(onPress);
  press.current = onPress;

  useEffect(() => {
    const el = host.current as HTMLElement | null;
    if (!el || map.current) return;
    const zoom = Math.round(Math.log2(360 / Math.max(initialRegion.longitudeDelta, 0.01)));
    const m = L.map(el, { zoomControl: true, attributionControl: true, maxBounds: [[5, 66], [37, 99]], minZoom: 4 })
      .setView([initialRegion.latitude, initialRegion.longitude], Math.max(4, Math.min(zoom, 12)));
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 18 }).addTo(m);
    m.on('click', (e: L.LeafletMouseEvent) => press.current({ nativeEvent: { coordinate: { latitude: e.latlng.lat, longitude: e.latlng.lng } } }));
    map.current = m;
    setTimeout(() => m.invalidateSize(), 50);
    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (marker.current) { marker.current.remove(); marker.current = null; }
    if (pin) {
      marker.current = L.circleMarker([pin.latitude, pin.longitude], { radius: 9, color: '#2563eb', weight: 3, fillColor: '#60a5fa', fillOpacity: 0.9 }).addTo(m);
    }
  }, [pin?.latitude, pin?.longitude]);

  return <View ref={host} style={style} />;
}
