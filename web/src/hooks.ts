import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from './api';
import type { DetectionProfile, Job, TelemetrySnapshot } from './types';
import { ExperimentRequestScope, type ExperimentTicket } from './experimentRequestScope';
export function useResource<T>(loader: () => Promise<T>, interval = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState<string | null>(null),
    [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const run = ++generation.current;
    try {
      const value = await loader();
      if (run === generation.current) {
        setData(value);
        setError(null);
      }
    } catch (e) {
      if (run === generation.current) setError(e instanceof Error ? e.message : '请求失败');
    } finally {
      if (run === generation.current) setLoading(false);
    }
  }, [loader]);
  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(true);
    void refresh();
    const timer = interval ? setInterval(() => void refresh(), interval) : null;
    return () => {
      generation.current++;
      if (timer) clearInterval(timer);
    };
  }, [refresh, interval]);
  return { data, error, loading, refresh, setData, setError };
}
export function useAction() {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string | null>(null);
  const run = async <T>(work: () => Promise<T>): Promise<T | undefined> => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      return await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run, setError };
}
export function useJob(scope = '') {
  const [job, setJob] = useState<Job | null>(null),
    action = useAction();
  const requestScope = useRef(new ExperimentRequestScope());
  requestScope.current.activate(scope);
  useEffect(() => {
    if (!job || ['completed', 'succeeded', 'failed', 'error', 'cancelled'].includes(job.status))
      return;
    let active = true;
    const ticket = requestScope.current.ticket();
    const timer = setInterval(() => {
      api
        .job(job.id)
        .then((v) => {
          if (active && requestScope.current.current(ticket)) setJob(v);
        })
        .catch((e) => {
          if (active && requestScope.current.current(ticket)) action.setError(e.message);
        });
    }, 1500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job?.id, job?.status]);
  const start = async (kind: 'tracking' | 'foul', id: string, profile?: DetectionProfile) => {
    const ticket = requestScope.current.submitted();
    const value = await action.run(() => api.createJob(kind, id, profile));
    if (value && requestScope.current.current(ticket)) setJob(value);
  };
  const hydrateJob = (value: Job, ticket: ExperimentTicket) => {
    if (requestScope.current.current(ticket)) setJob(value);
  };
  return {
    job,
    start,
    busy: action.busy,
    error: action.error,
    setJob,
    hydrateJob,
    hydrationTicket: () => requestScope.current.ticket(),
  };
}
export function useTelemetry() {
  const resource = useResource(api.telemetry, 4000);
  const [stream, setStream] = useState<'connecting' | 'connected' | 'offline'>('connecting');
  useEffect(() => {
    let socket: WebSocket | null = null,
      timer: ReturnType<typeof setTimeout> | null = null,
      disposed = false;
    function connect() {
      if (disposed) return;
      setStream('connecting');
      socket = new WebSocket(
        `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/telemetry`,
      );
      socket.onopen = () => {
        if (!disposed) setStream('connected');
      };
      socket.onmessage = (e) => {
        if (disposed) return;
        try {
          const payload = JSON.parse(e.data) as TelemetrySnapshot;
          if (payload.schema_version === 1) resource.setData(payload);
          resource.setError(null);
        } catch {
          /* Ignore malformed transport frames. */
        }
      };
      socket.onerror = () => {
        if (!disposed) setStream('offline');
      };
      socket.onclose = () => {
        if (disposed) return;
        setStream('offline');
        if (!disposed) timer = setTimeout(connect, 3000);
      };
    }
    timer = setTimeout(connect, 0);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, []);
  return { ...resource, stream };
}
