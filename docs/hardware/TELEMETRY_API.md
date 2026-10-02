# Showcase telemetry API v1

All HTTP responses below are complete snapshots. WebSocket messages use the same shape, so reconnecting clients can replace their state without reconstructing event history.

| Request | Body | Effect |
| --- | --- | --- |
| GET `/api/telemetry/snapshot` | none | Current state including stale flags |
| POST `/api/telemetry/config` | config patch | Validate geometry/ports; allowed only idle; clears histories |
| POST `/api/telemetry/connect` | optional `{"devices":["uwb","imu"]}` | Open configured ports; individual failures visible in connection states |
| POST `/api/telemetry/disconnect` | none | Stop readers/simulation, keep historical samples |
| POST `/api/telemetry/demo/start` | optional `{"rate_hz":10}` | Explicit synthetic UART simulation, 1–20 Hz; UART must be disconnected |
| POST `/api/telemetry/demo/stop` | none | Stop simulation; keep historical samples marked invalid |
| WS `/ws/telemetry` | none | Initial snapshot, then ≤10 Hz latest snapshots |

Snapshot top-level keys: `schema_version`, `source_mode`, `hardware_verified`, `updated_at`, `config`, `connections`, `uwb`, `imu`, `units`, `stream`, `evidence`. Timestamps are server receive time in Unix seconds, not synchronized sensor clocks. In each history, `source_mode` and timestamp remain attached to each sample.

`config` contains `anchors:[{id,x_m,y_m,z_m}]`, `tag_id`, `plane_height_m`, `history_limit` (default 240, cap 2000), `stale_after_s` (default 2), `maximum_residual_m` (default .5), and `serial:{uwb:{port,baudrate},imu:{port,baudrate}}`. Anchor IDs and order must be 0001, 0002, 0003; coordinates must be finite and share the configured plane height. Updating `serial` merges the selected device's parameters. Both absent ports are `null` initially.

`connections.uwb/imu` each contain `state`, `port`, `last_error`, `last_received_at`, `last_valid_at`, `stale`. State is `disconnected`, `connecting`, `connected`, `error` or `simulation`. The UI must use `uwb.position_valid` / `imu.acceleration_valid` before describing latest values as current; `connected` alone is insufficient.

`uwb` contains nullable `position:{x_m,y_m,z_m,tag_id,sequence,timestamp,residual_m,inside_anchor_triangle,quality,source_mode,stale}`, `position_valid`, three `ranges_m`, bounded `history`, `rejected_frames`, `last_rejection`, `protocol`, `checksum_available`. History samples omit dynamically evaluated `stale`; stale belongs to latest display state. `quality` is `good` or `degraded`, not a physical accuracy score.

`imu` contains nullable `acceleration:{x,y,z,raw_xyz,timestamp,unit,frame,gravity_included,source_mode,stale}`, `acceleration_valid`, bounded `history` and the same parser diagnostics. XYZ unit is `m/s²`, frame `vendor_sensor`, gravity included. `units` exposes position `m`, acceleration `m/s²`, both coordinate-frame labels and `gravity_included:true`.

Subscribers have a one-snapshot queue; slow clients replace their queued snapshot with the latest. `stream.dropped_snapshots` makes this visible. Subscriber capacity is 32, parser buffer cap 4096 bytes, histories configurable ≤2000. WebSocket disconnect releases its subscription. The owner must call service `start()` and `close()` in application lifespan, and run **one backend worker** to keep serial ownership and state consistent.

Example configuration for actual devices after identifying stable USB paths:

```json
{"serial":{"uwb":{"port":"/dev/serial/by-id/usb-UWB_ADAPTER_ID","baudrate":115200},"imu":{"port":"/dev/serial/by-id/usb-IMU_ADAPTER_ID","baudrate":115200}}}
```

These are example identifiers, never automatically selected or connected. Serial control endpoints should remain on the user-authorized private showcase network with the application's origin/access policy. The telemetry module does not configure system permissions or firmware, and does not enable arbitrary network serial bridges.
