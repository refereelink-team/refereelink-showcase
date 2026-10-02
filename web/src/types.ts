import type { MultiviewCase } from './types/multiview';
export interface Artifact {
  id: string;
  kind: string;
  mode?: string;
  label: string;
  url?: string;
}
export interface Clip {
  id: string;
  title: string;
  filename: string;
  duration_seconds: number;
  source_url: string;
  poster_url: string | null;
  artifacts: Artifact[];
}
export interface Catalog {
  cases: MultiviewCase[];
  clips: Clip[];
  backend?: { device: string; host?: string };
}
export interface Job {
  id: string;
  kind: 'tracking' | 'foul' | 'combined';
  case_id: string;
  status: string;
  progress: number;
  error?: string | null;
  artifacts: Artifact[];
  summary?: Record<string, unknown> | null;
}
export interface Anchor {
  id: string;
  x_m: number;
  y_m: number;
  z_m: number;
}
export interface Position {
  x_m: number;
  y_m: number;
  tag_id: number;
  timestamp: number;
  residual_m: number;
  stale?: boolean;
  quality: 'good' | 'degraded';
  source_mode: string;
}
export interface Acceleration {
  x: number;
  y: number;
  z: number;
  timestamp: number;
  unit: string;
  source_mode: string;
  stale?: boolean;
}
export interface SerialConnection {
  state: 'disconnected' | 'connecting' | 'connected' | 'error' | 'simulation';
  port: string | null;
  last_error: string | null;
  last_received_at: number | null;
  stale: boolean;
}
export interface TelemetryConfig {
  anchors: Anchor[];
  tag_id: number;
  plane_height_m: number;
  history_limit: number;
  stale_after_s: number;
  serial: {
    uwb: { port: string | null; baudrate: number };
    imu: { port: string | null; baudrate: number };
  };
}
export interface TelemetrySnapshot {
  schema_version: 1;
  source_mode: 'idle' | 'simulation' | 'serial';
  hardware_verified: boolean;
  updated_at: number;
  config: TelemetryConfig;
  connections: { uwb: SerialConnection; imu: SerialConnection };
  uwb: {
    position: Position | null;
    position_valid?: boolean;
    ranges_m: unknown[];
    history: Position[];
    rejected_frames: number;
    last_rejection: string | null;
  };
  imu: {
    acceleration: Acceleration | null;
    acceleration_valid?: boolean;
    history: Acceleration[];
    rejected_frames: number;
    last_rejection: string | null;
  };
  units: { position: string; acceleration: string };
  evidence: { notice: string };
}
