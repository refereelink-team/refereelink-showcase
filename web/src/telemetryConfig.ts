import type { Anchor, TelemetryConfig } from './types';

export function telemetryDevicePatch(
  anchors: Anchor[],
  tagId: number,
  transport: TelemetryConfig['imu_transport'],
  uwbPort: string,
  imuPort: string,
): Partial<TelemetryConfig> {
  return {
    anchors,
    tag_id: tagId,
    imu_transport: transport,
    serial: {
      uwb: { port: uwbPort.trim() || null, baudrate: 115200 },
      imu: { port: transport === 'usb' ? imuPort.trim() || null : null, baudrate: 115200 },
    },
  };
}
