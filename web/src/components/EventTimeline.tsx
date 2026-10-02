import type { CandidateRecord } from '../events';
export default function EventTimeline({
  events,
  duration,
  selected,
  onSelect,
  loading,
}: {
  events: CandidateRecord[];
  duration: number;
  selected: string | null;
  onSelect: (event: CandidateRecord) => void;
  loading: boolean;
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
              : '本次输出无候选事件'}
        </span>
      </div>
      <div className="event-timeline">
        <div className="timeline-track" />
        {events.map((record, index) => (
          <button
            key={record.event.id}
            className={`timeline-event ${selected === record.event.id ? 'selected' : ''}`}
            style={{
              left: `${Math.min(98, Math.max(2, (record.media_pts_seconds / Math.max(0.01, duration)) * 100))}%`,
            }}
            aria-label={`候选 ${index + 1}，${record.media_pts_seconds.toFixed(2)} 秒`}
            title={`${record.event.foul_details?.action || '候选事件'} · ${record.media_pts_seconds.toFixed(2)}s`}
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
              className={selected === record.event.id ? 'selected' : ''}
              onClick={() => onSelect(record)}
            >
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{record.event.foul_details?.action || '候选事件'}</strong>
              <small>{record.media_pts_seconds.toFixed(2)}s</small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
