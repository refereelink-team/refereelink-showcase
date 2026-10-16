import assert from 'node:assert/strict';
import test from 'node:test';
import { telemetryDevicePatch } from '../src/telemetryConfig.ts';

test('independent USB preserves two distinct physical port selections', () => {
  const patch = telemetryDevicePatch([], 5, 'usb', ' /dev/ttyUSB0 ', '/dev/ttyACM0');
  assert.equal(patch.imu_transport, 'usb');
  assert.equal(patch.serial?.uwb.port, '/dev/ttyUSB0');
  assert.equal(patch.serial?.imu.port, '/dev/ttyACM0');
});

test('bridge mode only configures A1 and does not leak a hidden USB port', () => {
  const patch = telemetryDevicePatch([], 9, 'uwb_bridge_v1', '/dev/ttyUSB0', '/dev/ttyACM0');
  assert.equal(patch.imu_transport, 'uwb_bridge_v1');
  assert.equal(patch.tag_id, 9);
  assert.deepEqual(patch.serial, {
    uwb: { port: '/dev/ttyUSB0', baudrate: 115200 },
    imu: { port: null, baudrate: 115200 },
  });
  assert.equal(telemetryDevicePatch([], 5, 'usb', '', '').serial?.imu.port, null);
});
