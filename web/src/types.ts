import type { MultiviewCase } from './types/multiview';
export type DetectionProfile =
  'legacy-v1' | 'mvit-full-v2' | 'mvit-pair-v2' | 'multidim-full-v2' | 'mvit-contact-v3';
export interface DetectorFingerprint {
  config_sha256: string;
  model_sha256: string;
  core_manifest_sha256: string;
  external_source_sha256: string;
}
export interface FoulQualification extends DetectorFingerprint {
  detection_profile: 'mvit-contact-v3';
  status: 'passed';
  scope: 'development_clip';
  evidence_sha256: string;
  verified_at: string;
}
export interface FoulConfiguration {
  default_profile: DetectionProfile;
  qualification?: FoulQualification | null;
  profiles: {
    id: DetectionProfile;
    label: string;
    model_id: string;
    available: boolean;
    experimental: boolean;
    qualification_scope?: 'development_clip' | null;
  }[];
}
export interface Artifact {
  id: string;
  kind: string;
  mode?: string;
  label: string;
  url?: string;
  detection_profile?: DetectionProfile;
  model_id?: string;
  qualification?: FoulQualification;
  detector_fingerprint?: DetectorFingerprint;
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
  foul_detection?: FoulConfiguration;
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
  detection_profile?: DetectionProfile;
  model_id?: string;
  qualification?: FoulQualification;
  detector_fingerprint?: DetectorFingerprint;
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
