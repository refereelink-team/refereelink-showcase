import type { Job } from '../types';

export interface TrackedEntity {
  track_id: number | string | null;
  entity_id: string | number | null;
  track_status: string;
  missing_frames: number;
  role: string;
  team: string;
  confidence: number | null;
  team_confidence: number | null;
  role_confidence: number | null;
  bbox: [number, number, number, number] | null;
  field_x: number | null;
  field_y: number | null;
  velocity_x: number | null;
  velocity_y: number | null;
}
export interface TrackedBall {
  status: 'fresh' | 'predicted' | 'stale' | 'unavailable';
  image_x: number | null;
  image_y: number | null;
  field_x: number | null;
  field_y: number | null;
  velocity_x: number | null;
  velocity_y: number | null;
  confidence: number | null;
  age_frames: number;
}
export interface TrackingFrame {
  frame_id: number;
  source_pts_s: number;
  homography_status: string;
  players: TrackedEntity[];
  ball: TrackedBall | null;
}
export interface TrackingPitch {
  length_m: number;
  width_m: number;
  penalty_area_length_m: number;
  penalty_area_width_m: number;
  goal_area_length_m: number;
  goal_area_width_m: number;
  center_circle_radius_m: number;
  penalty_spot_distance_m: number;
  goal_width_m: number;
}
export interface TrackingResult {
  id: string;
  revision: string;
  case_id: string;
  source: {
    url: string;
    width: number;
    height: number;
    fps: number;
    duration_seconds: number;
    decoded_frames: number;
  };
  pitch: TrackingPitch;
  frame_count: number;
  frames_url: string;
  metrics: {
    actual_fps: number | null;
    latency_ms: number | null;
    homography_available_frame_rate: number | null;
    projected_player_rate: number | null;
    unknown_role_rate: number | null;
    unknown_team_rate: number | null;
  };
  provenance: { device: 'cuda'; recorded_at: string | number | null; live: false };
  job_id: string | null;
}
export interface TrackingCase {
  case_id: string;
  source_url: string;
  poster_url: string | null;
  duration_seconds: number;
  decoded_frames: number;
  latest_result: TrackingResult | null;
  active_job: Job | null;
  result_warning: string | null;
}
export interface TrackingFramesPage {
  result_id: string;
  revision: string;
  offset: number;
  total: number;
  next_offset: number | null;
  frames: TrackingFrame[];
}
