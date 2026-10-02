import { useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import type {
  DefendsSide,
  EvidenceValue,
  FoulLocation,
  LocationGeometry,
  TeamLabel,
} from '../../types/multiview';
import './review-panels.css';

export interface FoulLocationPitchProps {
  location: FoulLocation | null;
  geometry: LocationGeometry | null;
  offenderTeam: EvidenceValue<TeamLabel> | null;
  homeDefendsSide: EvidenceValue<DefendsSide> | null;
  dirty: boolean;
  saving?: boolean;
  disabled?: boolean;
  compact?: boolean;
  onChange: (location: FoulLocation | null) => void;
  onSave?: () => void;
}

const clamp = (value: number, max: number) =>
  Math.round(Math.max(0, Math.min(max, value)) * 100) / 100;
function point(x: number, y: number): FoulLocation {
  return { x_m: clamp(x, 105), y_m: clamp(y, 68), source: 'human', confirmed: true };
}
function approximateZone(location: FoulLocation | null): string {
  if (!location) return '尚未设置犯规点';
  if (location.y_m >= 13.84 && location.y_m <= 54.16) {
    if (location.x_m <= 16.5) return '左侧禁区';
    if (location.x_m >= 88.5) return '右侧禁区';
  }
  if (Math.abs(location.x_m - 52.5) < 0.25) return '中线区域';
  return location.x_m < 52.5 ? '左半场' : '右半场';
}

export default function FoulLocationPitch({
  location,
  geometry,
  offenderTeam,
  homeDefendsSide,
  dirty,
  saving = false,
  disabled = false,
  compact = false,
  onChange,
  onSave,
}: FoulLocationPitchProps) {
  const [previous, setPrevious] = useState<FoulLocation | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const dragging = useRef(false);
  const offender = offenderTeam?.confirmed ? offenderTeam.value : null;
  const defendsSide = homeDefendsSide?.confirmed ? homeDefendsSide.value : null;

  function remember() {
    setPrevious(location ? { ...location } : null);
    setCanUndo(true);
  }
  function pointerPoint(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return point(
      ((event.clientX - bounds.left) / bounds.width) * 105,
      ((event.clientY - bounds.top) / bounds.height) * 68,
    );
  }
  function begin(event: PointerEvent<SVGSVGElement>) {
    if (disabled || event.button !== 0) return;
    remember();
    dragging.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    onChange(pointerPoint(event));
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    if (!disabled && dragging.current) onChange(pointerPoint(event));
  }
  function end(event: PointerEvent<SVGSVGElement>) {
    dragging.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }
  function keyboard(event: KeyboardEvent<SVGSVGElement>) {
    if (disabled) return;
    const directions: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const direction = directions[event.key];
    if (direction) {
      event.preventDefault();
      remember();
      const step = event.shiftKey ? 0.1 : 1;
      onChange(
        point(
          (location?.x_m ?? 52.5) + direction[0] * step,
          (location?.y_m ?? 34) + direction[1] * step,
        ),
      );
    }
  }
  function edit(axis: 'x_m' | 'y_m', raw: string) {
    remember();
    if (raw === '') {
      onChange(null);
      return;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    onChange(
      point(
        axis === 'x_m' ? value : (location?.x_m ?? 52.5),
        axis === 'y_m' ? value : (location?.y_m ?? 34),
      ),
    );
  }

  return (
    <section className="rv-pitch-panel" aria-label="犯规位置标注">
      <div className="rv-panel-heading">
        <h3>犯规位置</h3>
        {dirty && <span className="rv-unsaved">尚未保存</span>}
      </div>
      <svg
        className="rv-pitch"
        viewBox="0 0 105 68"
        role="application"
        tabIndex={disabled ? -1 : 0}
        aria-label="点击或拖动标注犯规位置，方向键调整，按住 Shift 精细调整"
        aria-disabled={disabled}
        onPointerDown={begin}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        onKeyDown={keyboard}
      >
        <rect className="rv-pitch-grass" x="0" y="0" width="105" height="68" rx="1.8" />
        <g className="rv-pitch-lines">
          <rect x="0.8" y="0.8" width="103.4" height="66.4" />
          <line x1="52.5" y1="0.8" x2="52.5" y2="67.2" />
          <circle cx="52.5" cy="34" r="9.15" />
          <rect x="0.8" y="13.84" width="15.7" height="40.32" />
          <rect x="88.5" y="13.84" width="15.7" height="40.32" />
          <rect x="0.8" y="24.84" width="4.7" height="18.32" />
          <rect x="99.5" y="24.84" width="4.7" height="18.32" />
          <circle cx="11" cy="34" r="0.45" />
          <circle cx="94" cy="34" r="0.45" />
          <circle cx="52.5" cy="34" r="0.45" />
        </g>
        {location && (
          <g className="rv-pitch-marker" transform={`translate(${location.x_m} ${location.y_m})`}>
            <circle r="2.1" />
            <line x1="-3.5" y1="0" x2="3.5" y2="0" />
            <line x1="0" y1="-3.5" x2="0" y2="3.5" />
          </g>
        )}
      </svg>
      <details className="rv-pitch-coordinate-details" open={compact ? undefined : true}>
        {compact && <summary>精确坐标</summary>}
        <div className="rv-pitch-coordinates">
          <label>
            X 坐标 (m)
            <input
              type="number"
              min="0"
              max="105"
              step="0.1"
              aria-label="犯规位置 X 坐标"
              disabled={disabled}
              value={location?.x_m ?? ''}
              placeholder="0–105"
              onChange={(event) => edit('x_m', event.target.value)}
            />
          </label>
          <label>
            Y 坐标 (m)
            <input
              type="number"
              min="0"
              max="68"
              step="0.1"
              aria-label="犯规位置 Y 坐标"
              disabled={disabled}
              value={location?.y_m ?? ''}
              placeholder="0–68"
              onChange={(event) => edit('y_m', event.target.value)}
            />
          </label>
        </div>
      </details>
      <div className="rv-pitch-readout">
        <span>{geometry?.zone ?? approximateZone(location)}</span>
        <div className="rv-pitch-actions">
          <button
            type="button"
            disabled={disabled || !location}
            onClick={() => {
              remember();
              onChange(null);
            }}
          >
            清除
          </button>
          <button
            type="button"
            disabled={disabled || !canUndo}
            onClick={() => {
              setCanUndo(false);
              onChange(previous);
            }}
          >
            撤销
          </button>
          {onSave && (
            <button type="button" disabled={disabled || !dirty || saving} onClick={onSave}>
              {saving ? '保存中…' : '保存'}
            </button>
          )}
        </div>
      </div>
      {!compact && (
        <p className="rv-pitch-context">
          犯规方 {offender === 'home' ? '主队' : offender === 'away' ? '客队' : '待确认'}
          {' · '}主队防守{' '}
          {defendsSide === 'left'
            ? '左侧球门'
            : defendsSide === 'right'
              ? '右侧球门'
              : '方向待确认'}
          {geometry?.in_offender_own_penalty_area === true
            ? ' · 犯规方本方禁区内'
            : geometry?.in_offender_own_penalty_area === false
              ? ' · 犯规方本方禁区外'
              : ''}
        </p>
      )}
      {location && (
        <span className={`rv-source-chip ${location.source === 'vision' ? 'model' : 'human'}`}>
          {location.source === 'vision' ? '视觉建议' : '人工标注'}
          {location.confirmed ? ' · 已确认' : ' · 待确认'}
        </span>
      )}
    </section>
  );
}
