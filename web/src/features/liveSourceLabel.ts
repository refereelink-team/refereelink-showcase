import type { LiveCameraStatus } from '../types/multiview';

export function liveSourceLabel(camera: LiveCameraStatus): string {
  if (camera.source_type !== 'field') return '固定机位 · RTSP';
  if (camera.video_transport === 'rtsp') {
    return camera.network_type === 'wiredEthernet' ? '手机 · 有线 RTSP' : '手机 · RTSP';
  }
  return camera.video_transport === 'srt' ? '手机 · SRT' : '手机输入';
}
