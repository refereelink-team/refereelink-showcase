import type { CandidateRecord } from '../events';
import { candidateEventTime, foulActionLabel } from '../events';
export default function EventTimeline({
  events,
  duration,
  selected,
  onSelect,
  loading,
  emptyMessage = '本次输出无候选事件',
  currentTime,
  onSeek,
  disabled = false,
}: {
  events: CandidateRecord[];
  duration: number;
  selected: string | null;
  onSelect: (event: CandidateRecord) => void;
  loading: boolean;
  emptyMessage?: string;
  currentTime?: number;
  onSeek?: (timeS: number) => void;
  disabled?: boolean;
}) {
  return (
    <section className="timeline-panel">
      <div className="section-line">
        <h2>事件时间线</h2>
        <span>
          {loading
            ? '读取候选记录…'
            : events.length
              ? `${events.length} 个模型候选 · 点击定位`
              : emptyMessage}
        </span>
      </div>
      <div className={`event-timeline ${onSeek ? 'interactive' : ''}`}>
        <div className="timeline-track" aria-hidden="true" />
        {onSeek && (
          <input
            className="event-timeline-seek"
            type="range"
            aria-label="事件时间线播放进度"
            aria-valuetext={`${(currentTime || 0).toFixed(2)} 秒，共 ${duration.toFixed(2)} 秒`}
            min="0"
            max={duration || 1}
            step="0.01"
            value={Math.max(0, Math.min(duration, currentTime || 0))}
            disabled={disabled || !duration}
            onChange={(e) => onSeek(Number(e.target.value))}
          />
        )}
        {events.map((record, index) => (
          <button
            key={record.event.id}
            disabled={disabled}
            className={`timeline-event ${selected === record.event.id ? 'selected' : ''}`}
            style={{
              left: `${Math.min(100, Math.max(0, (candidateEventTime(record) / Math.max(0.01, duration)) * 100))}%`,
            }}
            aria-label={`候选 ${index + 1}，${candidateEventTime(record).toFixed(2)} 秒`}
            title={`${foulActionLabel(record.event.foul_details?.action)} · ${candidateEventTime(record).toFixed(2)}s`}
            aria-pressed={selected === record.event.id}
            onClick={() => onSelect(record)}
          >
            <span />
          </button>
        ))}
        <div className="timeline-ticks">
          {[0, 0.25, 0.5, 0.75, 1].map((n) => (
            <span key={n}>{(n * duration).toFixed(1)}s</span>
          ))}
        </div>
      </div>
      {events.length > 0 && (
        <div className="event-list">
          {events.map((record, index) => (
            <button
              key={record.event.id}
              disabled={disabled}
              className={selected === record.event.id ? 'selected' : ''}
              aria-pressed={selected === record.event.id}
              onClick={() => onSelect(record)}
            >
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{foulActionLabel(record.event.foul_details?.action)}</strong>
              <small>{candidateEventTime(record).toFixed(2)}s</small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
