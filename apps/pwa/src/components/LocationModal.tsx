import { useEffect, useState } from 'react';
import { Building2, Check, ChevronLeft, ChevronRight, Crosshair, Loader2, Map as MapIcon, MapPin, RotateCw, Search, X } from 'lucide-react';
import { catalogsApi, type Place } from '../lib/api';
import { cx } from '../lib/format';
import { IndiaMap, type LatLng } from './IndiaMap';
import { Sheet } from './Sheet';

export type LocTab = 'gps' | 'map' | 'browse';

const TABS: { id: LocTab; label: string; Icon: typeof MapPin; hint?: string }[] = [
  { id: 'gps', label: 'GPS', Icon: Crosshair },
  { id: 'map', label: 'Map', Icon: MapIcon, hint: 'Needs internet' },
  { id: 'browse', label: 'Browse', Icon: Building2 },
];

const coordLabel = (p: LatLng) => `${p.lat.toFixed(3)}°N, ${p.lng.toFixed(3)}°E`;

/** City / district + state for a point (OpenStreetMap Nominatim). Falls back to coordinates offline. */
async function placeName(p: LatLng): Promise<string> {
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat=${p.lat}&lon=${p.lng}`, {
      headers: { 'Accept-Language': 'en' },
    });
    const j = await r.json();
    const a = j.address || {};
    return [a.city || a.town || a.state_district || a.county || a.village, a.state].filter(Boolean).join(', ') || coordLabel(p);
  } catch {
    return coordLabel(p);
  }
}

export function LocationModal({ open, initialTab = 'gps', onClose, onManual, onPin }: {
  open: boolean;
  initialTab?: LocTab;
  onClose: () => void;
  onManual: (stateCode?: string, cityCode?: string, label?: string) => void;
  onPin: (lat: number, lng: number, label?: string) => void;
}) {
  const [tab, setTab] = useState<LocTab>(initialTab);
  // GPS
  const [locating, setLocating] = useState(false);
  const [gps, setGps] = useState<LatLng | null>(null);
  const [gpsLabel, setGpsLabel] = useState('');
  const [gpsError, setGpsError] = useState('');
  // Map
  const [pin, setPin] = useState<LatLng | null>(null);
  const [pinLabel, setPinLabel] = useState('');
  const [geocoding, setGeocoding] = useState(false);
  // Browse
  const [states, setStates] = useState<Place[]>([]);
  const [statesError, setStatesError] = useState(false);
  const [cities, setCities] = useState<Place[] | null>(null);
  const [sel, setSel] = useState<Place | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setStatesError(false);
      catalogsApi.states().then(setStates).catch(() => setStatesError(true));
    } else {
      setGps(null); setGpsLabel(''); setGpsError('');
      setPin(null); setPinLabel('');
      setSel(null); setCities(null); setQuery('');
    }
  }, [open, initialTab]);

  const detect = () => {
    if (!('geolocation' in navigator)) { setGpsError('This browser cannot share its location.'); return; }
    setLocating(true); setGps(null); setGpsError('');
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const p = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        setGps(p);
        setGpsLabel(await placeName(p));
        setLocating(false);
      },
      (err) => {
        setLocating(false);
        setGpsError(err.code === err.PERMISSION_DENIED
          ? 'Location permission was denied. Allow it in your browser, or use Map / Browse.'
          : 'Could not get your location. Try again, or use Map / Browse.');
      },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 },
    );
  };

  const dropPin = async (p: LatLng) => {
    setPin(p); setPinLabel(''); setGeocoding(true);
    setPinLabel(await placeName(p));
    setGeocoding(false);
  };

  const pickState = async (st: Place) => {
    setSel(st); setCities(null); setQuery('');
    setCities(await catalogsApi.cities(st.code).catch(() => []));
  };

  const q = query.trim().toLowerCase();
  const stateList = q ? states.filter((s) => s.name.toLowerCase().includes(q)) : states;
  const cityList = cities && q ? cities.filter((c) => c.name.toLowerCase().includes(q)) : cities;

  return (
    <Sheet open={open} onClose={onClose} title="Set location" icon={<MapPin size={18} className="text-brand-pale" />} width="max-w-2xl">
      <div className="mb-5 grid grid-cols-3 gap-2">
        {TABS.map(({ id, label, Icon, hint }) => {
          const on = tab === id;
          return (
            <button key={id} type="button" onClick={() => setTab(id)}
              className={cx('flex flex-col items-center justify-center rounded-2xl border py-2.5 transition', on ? 'border-brand bg-gloss text-white shadow-glow' : 'border-navy-border bg-navy-mid text-mist-soft hover:text-white')}>
              <span className="flex items-center gap-1.5 text-sm font-semibold"><Icon size={15} /> {label}</span>
              {hint && <span className={cx('mt-0.5 text-[10px]', on ? 'text-white/75' : 'text-mist-dim')}>{hint}</span>}
            </button>
          );
        })}
      </div>

      {tab === 'gps' && (
        <div className="space-y-3 pb-2">
          {!gps ? (
            <>
              <div className="flex flex-col items-center gap-3 py-5 text-center">
                <span className="flex h-20 w-20 items-center justify-center rounded-full border border-brand/40 bg-[radial-gradient(circle,rgba(37,99,235,0.28),rgba(37,99,235,0.04))] text-brand-pale">
                  <Crosshair size={34} />
                </span>
                <div className="text-[17px] font-bold text-white">Detect my location</div>
                <p className="max-w-sm text-sm leading-relaxed text-mist-soft">Uses this device's location to find your state and city, so fines follow local rules.</p>
              </div>
              {gpsError && <p className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">{gpsError}</p>}
              <button type="button" onClick={detect} disabled={locating} className="btn-primary w-full">
                {locating ? <Loader2 size={18} className="animate-spin" /> : <Crosshair size={18} />}
                {locating ? 'Locating…' : 'Detect my location'}
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-3 rounded-2xl border border-brand/35 bg-brand/10 p-3.5">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gloss text-white"><MapPin size={16} /></span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold text-white">{gpsLabel || 'Finding place name…'}</div>
                  <div className="text-xs text-mist-soft">{coordLabel(gps)}</div>
                </div>
                <button type="button" onClick={detect} className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-mid text-mist-soft hover:text-white" aria-label="Detect again"><RotateCw size={15} /></button>
              </div>
              <button type="button" onClick={() => { onPin(gps.lat, gps.lng, gpsLabel); onClose(); }} className="btn-primary w-full">
                <Check size={18} /> Use this location
              </button>
            </>
          )}
        </div>
      )}

      {tab === 'map' && (
        <div className="space-y-3 pb-2">
          <div className="relative overflow-hidden rounded-2xl border border-navy-border">
            <IndiaMap pin={pin} onPick={dropPin} className="h-[260px] w-full lg:h-[340px]" />
            {!pin && (
              <span className="pointer-events-none absolute bottom-2 right-2 z-[500] rounded-lg border border-navy-border bg-navy/85 px-2.5 py-1.5 text-xs text-brand-pale">
                Tap anywhere in India to drop a pin
              </span>
            )}
          </div>
          {pin ? (
            <div className="flex items-center gap-3 rounded-2xl border border-brand/30 bg-brand/10 p-3">
              <MapPin size={18} className="text-brand-pale" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold text-white">{geocoding ? 'Finding place name…' : pinLabel}</div>
                <div className="text-xs text-mist-soft">{coordLabel(pin)}</div>
              </div>
              <button type="button" onClick={() => { setPin(null); setPinLabel(''); }} className="flex h-7 w-7 items-center justify-center rounded-full bg-navy-mid text-mist-soft hover:text-white" aria-label="Clear pin"><X size={13} /></button>
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-navy-border p-3 text-center text-sm text-mist-dim">No pin placed yet</div>
          )}
          <button type="button" disabled={!pin || geocoding} onClick={() => { if (pin) { onPin(pin.lat, pin.lng, pinLabel); onClose(); } }} className="btn-primary w-full">
            {geocoding ? 'Getting place name…' : 'Confirm location'}
          </button>
        </div>
      )}

      {tab === 'browse' && (
        <div className="pb-2">
          <div className="relative mb-3">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-mist-dim" />
            <input
              autoFocus={window.matchMedia('(pointer: fine)').matches}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={sel ? `Search cities in ${sel.name}` : 'Search states'}
              className="w-full rounded-xl border border-navy-border bg-navy-mid py-2.5 pl-9 pr-3 text-sm text-white placeholder:text-mist-dim focus:border-brand focus:outline-none"
            />
          </div>
          <div className="scroll-thin h-[min(50vh,420px)] overflow-y-auto">
            {!sel ? (
              statesError && !states.length ? (
                <p className="py-10 text-center text-sm text-mist-soft">States need the server once. Try GPS, or reconnect.</p>
              ) : !states.length ? (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-mist-soft"><Loader2 size={16} className="animate-spin" /> Loading states…</div>
              ) : (
                stateList.map((st) => (
                  <button key={st.code} type="button" onClick={() => pickState(st)} className="flex w-full items-center justify-between border-b border-navy-border/70 px-2 py-3 text-left text-[15px] text-mist hover:bg-white/[0.03] hover:text-white">
                    {st.name} <ChevronRight size={17} className="text-mist-dim" />
                  </button>
                ))
              )
            ) : (
              <>
                <button type="button" onClick={() => { setSel(null); setCities(null); setQuery(''); }} className="mb-1 flex w-full items-center gap-1.5 rounded-lg bg-brand/10 px-2 py-2.5 text-sm font-semibold text-brand-pale">
                  <ChevronLeft size={16} /> All states
                </button>
                <button type="button" onClick={() => { onManual(sel.code, undefined, sel.name); onClose(); }} className="flex w-full items-center justify-between border-b border-navy-border/70 px-2 py-3 text-left text-[15px] text-brand-pale hover:bg-white/[0.03]">
                  {sel.name} (whole state) <Check size={17} />
                </button>
                {cities === null && <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-brand" /></div>}
                {cityList?.map((c) => (
                  <button key={c.code} type="button" onClick={() => { onManual(sel.code, c.code, `${c.name}, ${sel.name}`); onClose(); }} className="flex w-full items-center justify-between border-b border-navy-border/70 px-2 py-3 text-left text-[15px] text-mist hover:bg-white/[0.03] hover:text-white">
                    {c.name} <ChevronRight size={17} className="text-mist-dim" />
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </Sheet>
  );
}
