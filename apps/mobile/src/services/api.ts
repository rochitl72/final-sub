/**
 * DriveLegal API Service
 * ----------------------
 * Thin Axios wrapper that:
 *  - Reads the base URL from config (defaults to http://127.0.0.1:8000)
 *  - Attaches the Bearer JWT on every request
 *  - Caches catalog responses in AsyncStorage for offline use
 *  - Retries once on network timeout (low-latency mode)
 */

import axios, { AxiosInstance, AxiosRequestConfig } from 'axios';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ─── Config ──────────────────────────────────────────────────────────────────
// When running on a physical device, set this to your Mac's local IP:
//   e.g.  export const BASE_URL = 'http://192.168.1.42:8000';
// The launch script (start.sh) auto-detects and writes the correct IP.

// @ts-ignore
const _envUrl = typeof process !== 'undefined' && process.env?.EXPO_PUBLIC_API_BASE_URL;
// Web (the PWA) is served by the backend itself → same-origin requests.
// The env URL (a LAN IP written for phone builds) must NOT be used on web: the
// page is already served by the backend, and a stale IP silently broke every
// request (New chat, translation, …).
export const BASE_URL: string = Platform.OS === 'web' ? '' : (_envUrl || 'http://127.0.0.1:8000');

const CACHE_TTL_MS = 7 * 24 * 3600 * 1000;   // catalogs cached 7 days
// F5 fix: bump CACHE_VERSION whenever catalog SHAPE changes (new vehicle
// segment, renamed category, etc.). It's part of the storage key, so old
// cached blobs are ignored immediately instead of lingering for up to 7 days.
const CACHE_VERSION = 'v2';

// ─── Axios instance ───────────────────────────────────────────────────────────

let _token: string | null = null;

export function setAuthToken(token: string | null) {
  _token = token;
}

export function createApiClient(): AxiosInstance {
  const client = axios.create({
    baseURL: BASE_URL,
    timeout: 15000,
    headers: { 'Content-Type': 'application/json' },
  });

  // Attach Bearer token on every request
  client.interceptors.request.use((config) => {
    if (_token) {
      config.headers.Authorization = `Bearer ${_token}`;
    }
    return config;
  });

  // Retry once on network error / timeout
  client.interceptors.response.use(
    (resp) => resp,
    async (err) => {
      const config = err.config as AxiosRequestConfig & { _retry?: boolean };
      if (!config._retry && (err.code === 'ECONNABORTED' || !err.response)) {
        config._retry = true;
        return client(config);
      }
      return Promise.reject(err);
    }
  );

  return client;
}

const api = createApiClient();

// ─── Cache helpers ────────────────────────────────────────────────────────────

async function _cacheSet(key: string, data: unknown): Promise<void> {
  try {
    await AsyncStorage.setItem(
      `@drivelegal_cache_${CACHE_VERSION}_${key}`,
      JSON.stringify({ data, ts: Date.now() })
    );
  } catch { /* storage error is non-fatal */ }
}

async function _cacheGet<T>(key: string): Promise<T | null> {
  try {
    const raw = await AsyncStorage.getItem(`@drivelegal_cache_${CACHE_VERSION}_${key}`);
    if (!raw) return null;
    const { data, ts } = JSON.parse(raw);
    if (Date.now() - ts > CACHE_TTL_MS) return null;
    return data as T;
  } catch {
    return null;
  }
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export interface AuthResponse {
  access_token: string;
  token_type:   string;
  expires_in:   number;
  user: {
    id:      string;
    email:   string;
    name:    string;
    picture: string;
  };
}

export interface User {
  id:      string;
  email:   string;
  name:    string;
  picture: string;
}

export const authApi = {
  registerDevice: async (deviceId: string): Promise<AuthResponse> => {
    const { data } = await api.post<AuthResponse>('/auth/device', {
      device_id: deviceId,
    });
    return data;
  },

  getMe: async (): Promise<User> => {
    const { data } = await api.get('/auth/me');
    return data;
  },
};

// ─── Sessions ─────────────────────────────────────────────────────────────────

export interface Session {
  id:           string;
  title:        string;
  mode:         'static' | 'dynamic';
  created_at:   number;
  updated_at:   number;
  last_snippet: string;
}

export interface SessionState {
  stage:              string;
  geo_mode:           string;
  state_code:         string | null;
  city_code:          string | null;
  city_name:          string | null;
  road_bucket:        string | null;
  vehicle_segment:    string | null;
  violation_category: string | null;
  violation_code:     string | null;
  last_fine_card:     FineCard | null;
  narrate_started:    boolean;
  mode:               string;
  has_prev_location?: boolean;
}

export interface FineCard {
  state_code:         string;
  city_code:          string | null;
  city_name:          string | null;
  road_bucket:        string | null;
  vehicle_segment:    string | null;
  violation_code:     string;
  violation_name:     string;
  section:            string | null;
  fine_first:         number | null;
  fine_repeat:        number | null;
  fine_max:           number | null;
  imprisonment_days:  number | null;
  license_action:     string | null;
  notes:              string | null;
}

export interface Chip {
  id:    string;
  label: string;
}

export interface DetailRow {
  label: string;
  value: string;
}

export interface Explanation {
  violation_matched?: string;
  match_method?:      string;
  match_confidence?:  string;
  fine_source?:       string;
  reasoning?:         string;
  data_sources?:      string[];
}

export interface TurnResponse {
  session_id:     string;
  intent:         string;
  scenario?:      any;
  /** This answer supersedes the previous one (replace it in the chat). */
  replace_last?:  boolean;
  reply?:         string;
  question?:      string;
  slot?:          string;
  chips?:         Chip[];
  /** When true, chips are checkboxes the user can multi-select before submit. */
  multi_select?:   boolean;
  /** `single` = radio MCQ; `multi` = checkbox MCQ. */
  selection_mode?: 'single' | 'multi';
  /** When true, show "Something else" with free-text input. */
  allow_other?:    boolean;
  allow_text?:     boolean;
  fine_card?:     FineCard;
  detail_table?:  DetailRow[];
  explanation?:   Explanation;
  mode?:          string;
  session_state:  SessionState;
}

export const sessionsApi = {
  list: async (): Promise<Session[]> => {
    const { data } = await api.get<{ sessions: Session[] }>('/api/sessions');
    return data.sessions;
  },

  create: async (mode: 'static' | 'dynamic'): Promise<TurnResponse> => {
    const { data } = await api.post('/api/sessions/new', { mode });
    return data;
  },

  history: async (id: string): Promise<{ messages: any[]; session_state: SessionState }> => {
    const { data } = await api.get(`/api/sessions/${id}/history`);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await api.delete(`/api/sessions/${id}`);
  },
};

// ─── Dialog / Turn ────────────────────────────────────────────────────────────

function _turnBody(extra: Record<string, unknown> = {}) {
  const body: Record<string, unknown> = { ...extra };
  if (_preferRules) body.prefer_rules = true;
  return body;
}

export const dialogApi = {
  turn: async (sessionId: string, message?: string, chipId?: string): Promise<TurnResponse> => {
    const body = _turnBody();
    if (message) body.message = message;
    if (chipId)  body.chip_id = chipId;
    const { data } = await api.post(`/api/session/${sessionId}/turn`, body);
    return data;
  },

  /** Submit a multi-select (checkbox) clarification — ids of every ticked option. */
  turnMulti: async (
    sessionId: string,
    chipIds: string[],
    otherText?: string,
  ): Promise<TurnResponse> => {
    const body = _turnBody({ chip_ids: chipIds });
    if (otherText) body.other_text = otherText;
    const { data } = await api.post(`/api/session/${sessionId}/turn`, body);
    return data;
  },

  setSlot: async (sessionId: string, slot: string, value: string): Promise<TurnResponse> => {
    const { data } = await api.post(`/api/session/${sessionId}/slot`, { slot, value });
    return data;
  },

  setMode: async (sessionId: string, mode: 'static' | 'dynamic'): Promise<TurnResponse> => {
    const { data } = await api.put(`/api/session/${sessionId}/mode`, { mode });
    return data;
  },

  setGeoMode: async (sessionId: string, mode: 'map' | 'chat'): Promise<TurnResponse> => {
    const { data } = await api.put(`/api/session/${sessionId}/geo_mode`, { mode });
    return data;
  },

  pinDrop: async (sessionId: string, lat: number, lng: number): Promise<TurnResponse> => {
    const { data } = await api.post(`/api/session/${sessionId}/geo/pin`, { lat, lng });
    return data;
  },

  manualLocation: async (
    sessionId: string,
    params: { state_code?: string; city_code?: string; text?: string }
  ): Promise<TurnResponse> => {
    const { data } = await api.post(`/api/session/${sessionId}/geo/manual`, params);
    return data;
  },

  revertLocation: async (sessionId: string): Promise<TurnResponse> => {
    const { data } = await api.post(`/api/session/${sessionId}/revert_location`);
    return data;
  },
};

// ─── Catalogs (with offline cache) ───────────────────────────────────────────

export interface StateItem {
  code: string;
  name: string;
}

export interface CityItem {
  code: string;
  name: string;
}

export interface VehicleItem {
  segment: string;
  label:   string;
  icon?:   string;
}

export interface ViolationCategory {
  category: string;
  label:    string;
  codes:    { code: string; name: string }[];
}

export const catalogsApi = {
  states: async (): Promise<StateItem[]> => {
    const cached = await _cacheGet<StateItem[]>('states');
    if (cached) return cached;
    const { data } = await api.get<{ states: StateItem[] }>('/api/catalogs/states');
    await _cacheSet('states', data.states);
    return data.states;
  },

  cities: async (stateCode: string): Promise<CityItem[]> => {
    const cached = await _cacheGet<CityItem[]>(`cities_${stateCode}`);
    if (cached) return cached;
    const { data } = await api.get<{ cities: CityItem[] }>(`/api/catalogs/cities?state=${stateCode}`);
    await _cacheSet(`cities_${stateCode}`, data.cities);
    return data.cities;
  },

  vehicles: async (): Promise<VehicleItem[]> => {
    const cached = await _cacheGet<VehicleItem[]>('vehicles');
    if (cached) return cached;
    const { data } = await api.get<{ vehicles: VehicleItem[] }>('/api/catalogs/vehicles');
    await _cacheSet('vehicles', data.vehicles);
    return data.vehicles;
  },

  violationCategories: async (): Promise<ViolationCategory[]> => {
    const cached = await _cacheGet<ViolationCategory[]>('violation_categories');
    if (cached) return cached;
    const { data } = await api.get<{ categories: ViolationCategory[] }>('/api/catalogs/violation_categories');
    await _cacheSet('violation_categories', data.categories);
    return data.categories;
  },

  warmup: async (): Promise<void> => {
    // Pre-fetch all catalogs on app launch for fully offline use
    try {
      await Promise.all([
        catalogsApi.states(),
        catalogsApi.vehicles(),
        catalogsApi.violationCategories(),
      ]);
    } catch { /* offline — rely on cache */ }
  },
};

// ─── Health ───────────────────────────────────────────────────────────────────

export interface HealthStatus {
  app?:          string;
  status?:       string;
  ready:         boolean;
  groq_ok:       boolean;
  sarvam_ok?:    boolean;
  ollama_ok:     boolean;
  model_present: boolean;
}

export const healthApi = {
  check: async (): Promise<HealthStatus> => {
    try {
      // Backend now returns instantly from cache — 3s is generous for a local server
      const { data } = await api.get<HealthStatus>('/api/health', { timeout: 3000 });
      return data;
    } catch {
      return {
        status: 'error',
        ready: false,
        groq_ok: false,
        sarvam_ok: false,
        ollama_ok: false,
        model_present: false,
      };
    }
  },
};

/** When true, chatbot turns skip Groq and use server rule engine only. */
let _preferRules = false;

export function setPreferRulesMode(on: boolean) {
  _preferRules = on;
}

// ─── Update / patch API ────────────────────────────────────────────────────────

export interface UpdateStatus {
  last_run_at:    number | null;
  days_since_run: number | null;
  patch_count:    number;
  last_status:    string | null;
  pages_checked:  number;
  patches_added:  number;
  is_running:     boolean;
}

export interface DataPatch {
  id:               string;
  state_code:       string | null;
  violation_code:   string;
  patch_type:       string;
  old_fine_first:   number | null;
  new_fine_first:   number | null;
  old_fine_repeat:  number | null;
  new_fine_repeat:  number | null;
  effective_date:   string | null;
  rule_summary:     string | null;
  source_domain:    string;
  groq_confidence:  number;
  scraped_at:       number;
}

export const updateApi = {
  status: async (): Promise<UpdateStatus | null> => {
    try {
      const { data } = await api.get('/api/update/status', { timeout: 8000 });
      return data;
    } catch {
      return null;
    }
  },

  run: async (): Promise<{ ok: boolean; message: string }> => {
    const { data } = await api.post('/api/update/run', {}, { timeout: 10000 });
    return data;
  },

  getPatches: async (): Promise<DataPatch[]> => {
    try {
      const { data } = await api.get('/api/update/patches', { timeout: 15000 });
      return data.patches ?? [];
    } catch {
      return [];
    }
  },
};

export default api;
