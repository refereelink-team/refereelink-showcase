export interface CandidateRecord {
  media_pts_seconds: number;
  source_frame_id: number;
  event: {
    id: string;
    event_type: 'foul_candidate';
    confidence: number;
    reviewed: boolean;
    foul_details?: { action?: string; severity?: string };
    evidence?: { source?: string };
  };
}
export function parseCandidateEvents(text: string): CandidateRecord[] {
  const records: CandidateRecord[] = [];
  text.split(/\r?\n/).forEach((line, index) => {
    if (!line.trim()) return;
    const value = JSON.parse(line) as CandidateRecord;
    if (value.event?.event_type !== 'foul_candidate') return;
    if (
      !Number.isFinite(value.media_pts_seconds) ||
      value.media_pts_seconds < 0 ||
      typeof value.event.id !== 'string' ||
      !Number.isFinite(value.event.confidence)
    ) {
      throw new Error(`候选记录格式错误：第 ${index + 1} 行`);
    }
    records.push(value);
  });
  return records.sort((a, b) => a.media_pts_seconds - b.media_pts_seconds);
}
