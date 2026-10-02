import type { TrackingCase, TrackingFramesPage, TrackingResult } from './types/tracking';
import type { Catalog, Job, TelemetrySnapshot, TelemetryConfig } from './types';
import type {
  LiveMultiviewStatus,
  MultiviewDecision,
  ReviewRecord,
  ReviewPreview,
  FoulFacts,
  ReviewState,
  ExplanationResponse,
  MultiviewStatus,
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
    if (!response.ok) {
      const detail = payload?.detail;
      const conflict = typeof detail === 'object' && detail?.code === 'REVIEW_REVISION_CONFLICT';
      throw new Error(
        conflict
          ? `复核修订冲突：服务器已是修订 ${detail.current_revision}。草稿已保留，请读取最新复核后再保存。`
          : typeof detail === 'string'
            ? detail
            : typeof payload?.error === 'string'
              ? payload.error
              : `服务暂不可用 (${response.status})`,
      );
    }
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
  multiviewStatus: () => request<MultiviewStatus>('/api/multiview/status'),
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
  reviewHistory: (id: string) =>
    request<{ count: number; history: ReviewRecord[] }>(
      `/api/multiview/cases/${encodeURIComponent(id)}/review/history`,
    ),
  previewReview: (id: string, facts: FoulFacts) =>
    post<ReviewPreview>(`/api/multiview/cases/${encodeURIComponent(id)}/review/preview`, { facts }),
  saveReview: (
    id: string,
    body: {
      expected_revision: number;
      analysis_id: string | null;
      facts: FoulFacts;
      review_state: ReviewState;
      preserve_unknowns?: boolean;
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
  trackingCase: (id: string) =>
    request<TrackingCase>(`/api/tracking/cases/${encodeURIComponent(id)}`),
  startTracking: (id: string) => post<Job>(`/api/tracking/cases/${encodeURIComponent(id)}/jobs`),
  trackingResult: (id: string) =>
    request<TrackingResult>(`/api/tracking/results/${encodeURIComponent(id)}`),
  trackingFrames: (id: string, revision: string, offset: number) =>
    request<TrackingFramesPage>(
      `/api/tracking/results/${encodeURIComponent(id)}/frames?offset=${offset}&limit=240&revision=${encodeURIComponent(revision)}`,
    ),
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
