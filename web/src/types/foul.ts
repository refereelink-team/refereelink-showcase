import type { CandidateRecord } from '../events';
import type { DetectionProfile, DetectorFingerprint } from '../types';

export interface FoulPlayer {
  track_id: number | null;
  entity_id?: number | null;
  team: string;
  role: string;
  bbox: [number, number, number, number] | null;
  confidence: number | null;
  track_status: string;
  missing_frames?: number;
}
export interface FoulFrame {
  frame_id: number;
  time_s: number;
  players: FoulPlayer[];
}
export interface FoulResult {
  schema_version: 1;
  id: string;
  revision: string;
  case_id: string;
  detection_profile: DetectionProfile;
  model_id: string;
  detector_fingerprint?: DetectorFingerprint;
  source: {
    url: string;
    width: number;
    height: number;
    duration_s: number;
    fps: number;
    sha256: string;
  };
  frame_count: number;
  frames_url: string;
  events: CandidateRecord[];
}
export interface FoulFramesPage {
  result_id: string;
  revision: string;
  offset: number;
  total: number;
  next_offset: number | null;
  frames: FoulFrame[];
}

export interface FoulPreparationClip {
  case_id: string;
  status: 'disabled' | 'preparing' | 'ready' | 'error';
  job_status: string | null;
  progress: number;
  job_id: string | null;
  result_id: string | null;
  revision: string | null;
  reason: string | null;
}
export interface FoulPreparation {
  schema_version: 1;
  boot_id: string;
  enabled: boolean;
  status: FoulPreparationClip['status'];
  detection_profile: 'mvit-contact-v3';
  model_id: string;
  reason: string | null;
  clips: FoulPreparationClip[];
}
