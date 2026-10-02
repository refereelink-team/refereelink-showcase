export type CalibrationLabel =
  'home_outfield' | 'away_outfield' | 'home_goalkeeper' | 'away_goalkeeper' | 'referee' | 'ignore';
export type CalibrationFilter =
  'all' | 'unlabelled' | 'home' | 'away' | 'roles' | 'ignored' | 'weak';
export type CalibrationBox = [number, number, number, number];
export interface CalibrationObservation {
  track_id: number;
  bbox: CalibrationBox;
  confidence: number;
  quality_accepted: boolean;
  quality_score: number;
}
export interface CalibrationFrame {
  frame_index: number;
  timestamp_ms: number;
  players: CalibrationObservation[];
}
export interface CalibrationTrack {
  track_id: number;
  first_timestamp_ms: number;
  last_timestamp_ms: number;
  observation_count: number;
  quality_observation_count: number;
  representative_frame_index: number;
  representative_timestamp_ms: number;
  representative_bbox: CalibrationBox;
  representative_quality_score: number;
  label?: CalibrationLabel | null;
  sample_count?: number;
  quality_score?: number;
}
export interface CalibrationMetadata {
  session_id: string;
  clip_id: string;
  fps: number;
  width: number;
  height: number;
  frame_count: number;
  duration_ms: number;
  frames: CalibrationFrame[];
  tracks: CalibrationTrack[];
}
export interface CalibrationReport {
  passed: boolean;
  reasons: string[];
  home_track_count: number;
  away_track_count: number;
  home_sample_count: number;
  away_sample_count: number;
  min_samples_per_track: number;
  min_tracks_per_team: number;
  home_eligible_track_count: number;
  away_eligible_track_count: number;
  excluded_track_ids: number[];
  goalkeeper_mapping_ready: boolean;
  referee_mapping_ready: boolean;
  leave_one_track_out_accuracy: number | null;
}
export interface CalibrationSnapshot {
  id: string;
  revision: number;
  status: string;
  operation: string | null;
  error: string | null;
  source_url: string;
  video_url: string;
  metadata_url: string | null;
  source: { duration_ms: number; fps: number; width: number; height: number };
  session: {
    state: string;
    ready: boolean;
    processing_progress: number;
    observed_frames: number;
    clip_id: string | null;
    clip_start_ms: number | null;
    clip_end_ms: number | null;
    clip_duration_ms: number | null;
    tracks: CalibrationTrack[];
    validation_report: CalibrationReport | null;
  };
  labels: Record<string, CalibrationLabel>;
  auto_ignored_track_ids?: number[];
  demo: { ready: boolean; protected: true };
}
