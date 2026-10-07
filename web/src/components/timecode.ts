export function timecode(value: number) {
  if (!Number.isFinite(value)) return '0:00';
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
}
