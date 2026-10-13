/**
 * DriveLegal API client
 * ─────────────────────
 * The PWA is served by the FastAPI backend itself, so every request is same-origin
 * (in `npm run dev`, Vite proxies /api and /auth to the backend).
 *  - attaches the device JWT on every request
 *  - retries once on a network error / timeout
 *  - caches catalog responses in localStorage for offline use
 */
import axios, { AxiosRequestConfig } from 'axios';
import { storage } from './storage';

export const BASE_URL = '';

const CACHE_TTL_MS = 7 * 24 * 3600 * 1000; // catalogs cached 7 days
// Bump when the catalog SHAPE changes so stale blobs are ignored immediately.
const CACHE_VERSION = 'v2';

// ─── HTTP client ────────────────────────────────────────────────────────────

let _token: string | null = null;
export function setAuthToken(token: string | null) { _token = token; }

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  if (_token) config.headers.Authorization = `Bearer ${_token}`;
  return config;
});

api.interceptors.response.use(
  (resp) => resp,
  async (err) => {
    const config = err.config as AxiosRequestConfig & { _retry?: boolean };
    if (config && !config._retry && (err.code === 'ECONNABORTED' || !err.response)) {
      config._retry = true;
      return api(config);
    }
    return Promise.reject(err);
  },
);

// ─── Cache helpers ──────────────────────────────────────────────────────────

function cacheSet(key: string, data: unknown) {
  storage.setJSON(`@drivelegal_cache_${CACHE_VERSION}_${key}`, { data, ts: Date.now() });
}
function cacheGet<T>(key: string): T | null {
  const hit = storage.getJSON<{ data: T; ts: number }>(`@drivelegal_cache_${CACHE_VERSION}_${key}`);
  if (!hit || Date.now() - hit.ts > CACHE_TTL_MS) return null;
  return hit.data;
}

// ─── Types ──────────────────────────────────────────────────────────────────

export interface User { id: string; email: string; name: string; picture: string }
export interface AuthResponse { access_token: string; token_type: string; expires_in: number; user: User }

export type SessionMode = 'static' | 'dynamic';

export interface Session {
  id: string;
  title: string;
  mode: SessionMode;
  created_at: number;
  updated_at: number;
  last_snippet: string;
}

export interface FineCard {
  state_code: string;
  city_code: string | null;
  city_name: string | null;
  road_bucket: string | null;
  vehicle_segment: string | null;
  violation_code: string;
  violation_name: string;
  section: string | null;
  fine_first: number | null;
  fine_repeat: number | null;
  fine_max: number | null;
  imprisonment_days: number | null;
  license_action: string | null;
  notes: string | null;
  mv_section?: string | null;
}

export interface SessionState {
  stage: string;
  geo_mode: string;
  state_code: string | null;
  city_code: string | null;
  city_name: string | null;
  road_bucket: string | null;
  vehicle_segment: string | null;
  violation_category: string | null;
  violation_code: string | null;
  last_fine_card: FineCard | null;
  narrate_started: boolean;
  mode: string;
  has_prev_location?: boolean;
}

export interface Chip { id: string; label: string }
export interface DetailRow { label: string; value: string }
export interface Explanation {
  violation_matched?: string;
  match_method?: string;
  match_confidence?: string;
  fine_source?: string;
  reasoning?: string;
  data_sources?: string[];
}

/** Multi-person breakdown produced by the backend Scenario Engine. */
export interface ScenarioOffence {
  code: string;
  name: string;
  section?: string;
  fine_first?: number | null;
  fine_repeat?: number | null;
  imprisonment?: string | null;
  compoundable?: boolean;
  licence_action?: string | null;
  certainty?: string;
  deemed?: boolean;
  juvenile?: boolean;
  severity?: string;
}
export interface ScenarioPerson {
  id?: string;
  label?: string;
  age?: number | null;
  roles?: string[];
  kind?: string;
  total_first?: number | null;
  offences?: ScenarioOffence[];
}
export interface Scenario { head?: string | null; tail?: string | null; people?: ScenarioPerson[]; [k: string]: unknown }

export interface TurnResponse {
  session_id: string;
  intent: string;
  scenario?: Scenario;
  /** This answer supersedes the previous one (replace it in the chat). */
  replace_last?: boolean;
  /** The server is still refining this answer with the LLM; poll /updates. */
  refining?: boolean;
  reply?: string;
  question?: string;
  slot?: string;
  chips?: Chip[];
  multi_select?: boolean;
  selection_mode?: 'single' | 'multi';
  allow_other?: boolean;
  allow_text?: boolean;
  fine_card?: FineCard;
  detail_table?: DetailRow[];
  explanation?: Explanation;
  mode?: string;
  session_state: SessionState;
}

export interface HistoryMessage { role: 'user' | 'assistant' | 'system'; content: string; created_at: number; payload?: any }

// ─── Auth ───────────────────────────────────────────────────────────────────

export const authApi = {
  registerDevice: async (deviceId: string) =>
    (await api.post<AuthResponse>('/auth/device', { device_id: deviceId })).data,
  getMe: async () => (await api.get<User>('/auth/me')).data,
};

// ─── Sessions ───────────────────────────────────────────────────────────────

export const sessionsApi = {
  list: async () => (await api.get<{ sessions: Session[] }>('/api/sessions')).data.sessions,
  create: async (mode: SessionMode) => (await api.post<TurnResponse>('/api/sessions/new', { mode })).data,
  history: async (id: string) =>
    (await api.get<{ messages: HistoryMessage[]; session_state: SessionState }>(`/api/sessions/${id}/history`)).data,
  delete: async (id: string) => { await api.delete(`/api/sessions/${id}`); },
};

// ─── Dialog ─────────────────────────────────────────────────────────────────

/** When true, chatbot turns skip the LLM and use the server rule engine only. */
let _preferRules = false;
export function setPreferRulesMode(on: boolean) { _preferRules = on; }

function turnBody(extra: Record<string, unknown> = {}) {
  const body: Record<string, unknown> = { ...extra };
  if (_preferRules) body.prefer_rules = true;
  return body;
}

export const dialogApi = {
  turn: async (sessionId: string, message?: string, chipId?: string) => {
    const body = turnBody();
    if (message) body.message = message;
    if (chipId) body.chip_id = chipId;
    return (await api.post<TurnResponse>(`/api/session/${sessionId}/turn`, body)).data;
  },
  /** Answers refined in the background by the self-hosted LLM. */
  updates: async (sessionId: string) =>
    (await api.get<{ updates: TurnResponse[]; refining: boolean }>(`/api/session/${sessionId}/updates`)).data,
  turnMulti: async (sessionId: string, chipIds: string[], otherText?: string) => {
    const body = turnBody({ chip_ids: chipIds });
    if (otherText) body.other_text = otherText;
    return (await api.post<TurnResponse>(`/api/session/${sessionId}/turn`, body)).data;
  },
  setMode: async (sessionId: string, mode: SessionMode) =>
    (await api.put<TurnResponse>(`/api/session/${sessionId}/mode`, { mode })).data,
  pinDrop: async (sessionId: string, lat: number, lng: number) =>
    (await api.post<TurnResponse>(`/api/session/${sessionId}/geo/pin`, { lat, lng })).data,
  manualLocation: async (sessionId: string, params: { state_code?: string; city_code?: string; text?: string }) =>
    (await api.post<TurnResponse>(`/api/session/${sessionId}/geo/manual`, params)).data,
  revertLocation: async (sessionId: string) =>
    (await api.post<TurnResponse>(`/api/session/${sessionId}/revert_location`)).data,
};

// ─── Catalogs (offline-cached) ──────────────────────────────────────────────

export interface Place { code: string; name: string }

async function cached<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const hit = cacheGet<T>(key);
  if (hit) return hit;
  const data = await fetcher();
  cacheSet(key, data);
  return data;
}

export const catalogsApi = {
  states: () => cached('states', async () => (await api.get<{ states: Place[] }>('/api/catalogs/states')).data.states),
  cities: (state: string) =>
    cached(`cities_${state}`, async () => (await api.get<{ cities: Place[] }>(`/api/catalogs/cities?state=${state}`)).data.cities),
  vehicles: () => cached('vehicles', async () => (await api.get<{ vehicles: unknown[] }>('/api/catalogs/vehicles')).data.vehicles),
  violationCategories: () =>
    cached('violation_categories', async () => (await api.get<{ categories: unknown[] }>('/api/catalogs/violation_categories')).data.categories),
  /** Pre-fetch everything the app needs offline. */
  warmup: async () => {
    try { await Promise.all([catalogsApi.states(), catalogsApi.vehicles(), catalogsApi.violationCategories()]); } catch { /* offline */ }
  },
};

// ─── Health ─────────────────────────────────────────────────────────────────

export interface HealthStatus {
  app?: string;
  status?: string;
  ready: boolean;
  groq_ok: boolean;
  llm_ok?: boolean;
  llm_provider?: string;
  sarvam_ok?: boolean;
  model?: string;
}

export const healthApi = {
  check: async (): Promise<HealthStatus> => {
    try {
      return (await api.get<HealthStatus>('/api/health', { timeout: 3000 })).data;
    } catch {
      return { status: 'error', ready: false, groq_ok: false, sarvam_ok: false };
    }
  },
};

// ─── Law-dataset updates ────────────────────────────────────────────────────

export interface UpdateStatus {
  last_run_at: number | null;
  days_since_run: number | null;
  patch_count: number;
  last_status: string | null;
  pages_checked: number;
  patches_added: number;
  is_running: boolean;
}

export const updateApi = {
  status: async (): Promise<UpdateStatus | null> => {
    try { return (await api.get<UpdateStatus>('/api/update/status', { timeout: 8000 })).data; } catch { return null; }
  },
  run: async () => (await api.post<{ ok: boolean; message: string }>('/api/update/run', {}, { timeout: 10000 })).data,
};

export default api;
