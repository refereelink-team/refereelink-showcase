import type { Catalog, Job, TelemetrySnapshot, TelemetryConfig } from './types';
import type {
  LiveMultiviewStatus,
  MultiviewDecision,
  ReviewRecord,
  FoulFacts,
  ReviewState,
  ExplanationResponse,
} from './types/multiview';
export async function request<T>(
  path: string,
  options?: RequestInit,
  timeoutMs = 15000,
): Promise<T> {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...options,
      signal: options?.signal || controller.signal,
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok)
      throw new Error(
        typeof payload?.detail === 'string'
          ? payload.detail
          : typeof payload?.error === 'string'
            ? payload.error
            : `服务暂不可用 (${response.status})`,
      );
    if (payload === null) throw new Error('服务返回了空响应');
    return payload as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new Error('后台响应超时，请稍后重试');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
export const post = <T>(path: string, body?: unknown, timeoutMs?: number) =>
  request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    timeoutMs,
  );
export const api = {
  catalog: () => request<Catalog>('/api/catalog'),
  live: () => request<LiveMultiviewStatus>('/api/multiview/live/status'),
  liveControl: (action: 'start' | 'stop') =>
    post<LiveMultiviewStatus>(`/api/multiview/live/${action}`),
  trigger: () => post<{ case_id: string; capture_state: string }>('/api/multiview/live/trigger'),
  analyze: (id: string) =>
    post<{ status: string; message: string; decision: MultiviewDecision | null }>(
      '/api/multiview/analyze',
      { case_id: id, device: 'cuda' },
      120000,
    ),
  review: (id: string) =>
    request<{ review: ReviewRecord | null; analysis: MultiviewDecision | null }>(
      `/api/multiview/cases/${encodeURIComponent(id)}/review`,
    ),
  saveReview: (
    id: string,
    body: {
      expected_revision: number;
      analysis_id: string | null;
      facts: FoulFacts;
      review_state: ReviewState;
    },
  ) =>
    request<{ review: ReviewRecord }>(`/api/multiview/cases/${encodeURIComponent(id)}/review`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  explain: (id: string, revision: number) =>
    post<ExplanationResponse>(`/api/multiview/cases/${encodeURIComponent(id)}/explanation`, {
      revision,
      use_llm: true,
    }),
  createJob: (kind: 'tracking' | 'foul', case_id: string) =>
    post<Job>('/api/experiments/jobs', { kind, case_id }),
  job: (id: string) => request<Job>(`/api/experiments/jobs/${encodeURIComponent(id)}`),
  jobs: () => request<{ jobs: Job[] }>('/api/experiments/jobs'),
  telemetry: () => request<TelemetrySnapshot>('/api/telemetry/snapshot'),
  telemetryAction: (action: 'demo/start' | 'demo/stop' | 'connect' | 'disconnect') =>
    post<TelemetrySnapshot>(`/api/telemetry/${action}`),
  telemetryConfig: (body: Partial<TelemetryConfig>) =>
    post<TelemetrySnapshot>('/api/telemetry/config', body),
};
export function mediaUrl(path?: string | null) {
  if (!path) return undefined;
  if (path.startsWith('http')) {
    try {
      const url = new URL(path);
      if (url.pathname.startsWith('/api/multiview/')) return url.pathname + url.search;
    } catch {
      /* keep same-origin relative urls */
    }
  }
  return path;
}
