import assert from 'node:assert/strict';
import test from 'node:test';
import { liveSourceLabel } from '../src/features/liveSourceLabel.ts';

test('RTSP alone does not prove Ethernet; actual sender observation is required', () => {
  assert.equal(liveSourceLabel({ source_type: 'field', video_transport: 'rtsp' } as any), '手机 · RTSP');
  assert.equal(liveSourceLabel({ source_type: 'field', video_transport: 'rtsp', network_type: 'wiredEthernet' } as any), '手机 · 有线 RTSP');
});
test('legacy and unavailable transports retain truthful presentation', () => {
  assert.equal(liveSourceLabel({ source_type: 'field' } as any), '手机输入');
  assert.equal(liveSourceLabel({ source_type: 'field', video_transport: 'srt' } as any), '手机 · SRT');
  assert.equal(liveSourceLabel({ source_type: 'rtsp' } as any), '固定机位 · RTSP');
});
