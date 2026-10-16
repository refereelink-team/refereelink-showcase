import { useEffect, useState } from 'react';
import { api } from '../api';
import { useAction, useTelemetry } from '../hooks';
import type { Anchor, TelemetryConfig, TelemetrySnapshot, Acceleration } from '../types';
import { telemetryDevicePatch } from '../telemetryConfig';
import { Alert, Empty, Footer, Modes, PageHeader } from '../components/UI';
const states: Record<string, string> = {
  disconnected: '未连接',
  connecting: '连接中',
  connected: '串口已打开',
  error: '连接错误',
  simulation: '演示回放',
};
function Plane({ snapshot }: { snapshot: TelemetrySnapshot }) {
  const anchors = snapshot.config.anchors,
    position = snapshot.uwb.position,
    history = snapshot.uwb.history;
  const all = [...anchors, ...history];
  const minX = Math.min(0, ...all.map((a) => a.x_m)) - 1,
    minY = Math.min(0, ...all.map((a) => a.y_m)) - 1,
    maxX = Math.max(8, ...all.map((a) => a.x_m)) + 1,
    maxY = Math.max(7, ...all.map((a) => a.y_m)) + 1;
  const px = (x: number) => 70 + ((x - minX) / (maxX - minX)) * 800,
    py = (y: number) => 650 - ((y - minY) / (maxY - minY)) * 570;
  const ticksX = Array.from({ length: Math.floor(maxX - minX) + 1 }, (_, i) => Math.ceil(minX) + i),
    ticksY = Array.from({ length: Math.floor(maxY - minY) + 1 }, (_, i) => Math.ceil(minY) + i);
  return (
    <svg className="position-plane" viewBox="0 0 920 730" role="img" aria-label="UWB平面位置与轨迹">
      <g>
        {ticksX.map((x) => (
          <g key={x}>
            <path d={`M${px(x)} 70V650`} stroke="#e8ecf1" />
            <text x={px(x)} y="680" textAnchor="middle">
              {x}
            </text>
          </g>
        ))}
        {ticksY.map((y) => (
          <g key={y}>
            <path d={`M70 ${py(y)}H870`} stroke="#e8ecf1" />
            <text x="48" y={py(y) + 5} textAnchor="end">
              {y}
            </text>
          </g>
        ))}
      </g>
      <path d="M70 70v580h800" stroke="#7d8491" fill="none" />
      <text x="470" y="717" textAnchor="middle">
        X (m)
      </text>
      <text x="20" y="365" textAnchor="middle" transform="rotate(-90 20 365)">
        Y (m)
      </text>
      {history.length > 1 && (
        <polyline
          points={history.map((p) => `${px(p.x_m)},${py(p.y_m)}`).join(' ')}
          fill="none"
          stroke="#65739b"
          strokeWidth="2"
        />
      )}
      {anchors.map((a, i) => (
        <g key={a.id}>
          <path d={`M${px(a.x_m)} ${py(a.y_m) - 9}l9 9-9 9-9-9Z`} fill="#baf45b" stroke="#80a834" />
          <text x={px(a.x_m) + 14} y={py(a.y_m) - 12}>{`A${i + 1}`}</text>
        </g>
      ))}
      {position && (
        <g>
          <circle
            cx={px(position.x_m)}
            cy={py(position.y_m)}
            r="7"
            fill={snapshot.uwb.position_valid === false ? '#b1b8c5' : '#1d2433'}
          />
          <text x={px(position.x_m)} y={py(position.y_m) + 28} textAnchor="middle">
            Tag {position.tag_id}
            {snapshot.uwb.position_valid === false ? ' · 已过期' : ''}
          </text>
        </g>
      )}
      {!position && (
        <text x="470" y="355" textAnchor="middle" fill="#7d8491">
          等待定位数据
        </text>
      )}
    </svg>
  );
}
function Waveform({ history }: { history: Acceleration[] }) {
  const limit = history.slice(-120),
    start = limit[0]?.timestamp || 0,
    end = limit.at(-1)?.timestamp || start + 1,
    range = Math.max(
      1,
      Math.ceil(
        Math.max(1, ...limit.flatMap((p) => [Math.abs(p.x), Math.abs(p.y), Math.abs(p.z)])) / 5,
      ) * 5,
    );
  const x = (t: number) => 35 + ((t - start) / Math.max(0.1, end - start)) * 305,
    y = (n: number) => 95 - (n / range) * 65;
  return (
    <svg viewBox="0 0 360 210" className="waveform" role="img" aria-label="三轴加速度波形">
      {[range, range / 2, 0, -range / 2, -range].map((value) => (
        <g key={value}>
          <path d={`M35 ${y(value)}H340`} stroke="#e8ecf1" />
          <text x="27" y={y(value) + 4} textAnchor="end">
            {value}
          </text>
        </g>
      ))}
      {[35, 96, 157, 218, 279, 340].map((v) => (
        <path key={v} d={`M${v} 30v130`} stroke="#e8ecf1" />
      ))}
      <path d="M35 30v130h305" stroke="#a6afbd" fill="none" />
      {(['x', 'y', 'z'] as const).map((axis, i) => (
        <polyline
          key={axis}
          points={limit.map((p) => `${x(p.timestamp)},${y(p[axis])}`).join(' ')}
          fill="none"
          stroke={['#3680ff', '#f29440', '#77b52d'][i]}
          strokeWidth="1.8"
        />
      ))}
      <text x="35" y="185">
        0
      </text>
      <text x="340" y="185" textAnchor="end">
        {Math.max(0, end - start).toFixed(1)}s
      </text>
      {!limit.length && (
        <text x="186" y="90" textAnchor="middle" fill="#7d8491">
          等待 IMU 数据
        </text>
      )}
    </svg>
  );
}
export default function Telemetry() {
  const telemetry = useTelemetry(),
    action = useAction();
  const [mode, setMode] = useState<'device' | 'demo'>('demo'),
    [anchors, setAnchors] = useState<Anchor[]>([]),
    [uwbPort, setUwbPort] = useState(''),
    [imuPort, setImuPort] = useState(''),
    [imuTransport, setImuTransport] = useState<TelemetryConfig['imu_transport']>('usb'),
    [tagId, setTagId] = useState(5),
    [configured, setConfigured] = useState(false),
    [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (telemetry.data && !configured) {
      setAnchors(telemetry.data.config.anchors);
      setUwbPort(telemetry.data.config.serial.uwb.port || '');
      setImuPort(telemetry.data.config.serial.imu.port || '');
      setImuTransport(telemetry.data.config.imu_transport ?? 'usb');
      setTagId(telemetry.data.config.tag_id);
      setConfigured(true);
    }
  }, [telemetry.data, configured]);
  const snapshot = telemetry.data,
    running = snapshot?.source_mode === 'simulation';
  async function command(command: 'demo/start' | 'demo/stop' | 'connect' | 'disconnect') {
    setNotice(null);
    const result = await action.run(() => api.telemetryAction(command));
    if (result) telemetry.setData(result);
  }
  async function save() {
    setNotice(null);
    const result = await action.run(() =>
      api.telemetryConfig(telemetryDevicePatch(anchors, tagId, imuTransport, uwbPort, imuPort)),
    );
    if (result) {
      telemetry.setData(result);
      setNotice('配置已保存');
    }
    return result;
  }
  async function connect() {
    const config = await save();
    if (config) {
      await command('connect');
    }
  }
  return (
    <>
      <div className="workspace telemetry-workspace">
        <main className="main-column">
          <PageHeader title="足球定位" description="UWB 平面位置与 IMU 运动感知">
            <Modes
              label="定位数据来源"
              value={mode}
              onChange={(next) => {
                setMode(next);
                if (next === 'device' && running) void command('demo/stop');
              }}
              options={[
                { value: 'device', label: '设备数据' },
                { value: 'demo', label: '演示回放' },
              ]}
            />
            {mode === 'demo' && (
              <button
                className="button primary"
                disabled={action.busy || !snapshot}
                onClick={() => void command(running ? 'demo/stop' : 'demo/start')}
              >
                {running ? '停止回放' : '开始回放'}
              </button>
            )}
          </PageHeader>
          <Alert
            message={telemetry.error || action.error}
            onRetry={() => void telemetry.refresh()}
          />
          {snapshot ? (
            <>
              <div className="plane-source">
                <span className={`signal ${running ? 'demo' : ''}`}>
                  {snapshot.source_mode === 'simulation'
                    ? '演示回放'
                    : snapshot.source_mode === 'serial'
                      ? '串口数据'
                      : '等待设备'}
                </span>
                <span>{telemetry.stream === 'connected' ? '数据通道在线' : '数据通道重连中'}</span>
              </div>
              <Plane snapshot={snapshot} />
              {snapshot.uwb.position && (
                <div
                  className={`position-readout ${snapshot.uwb.position_valid === false ? 'stale' : ''}`}
                >
                  <span>
                    X <strong>{snapshot.uwb.position.x_m.toFixed(2)}</strong> m
                  </span>
                  <span>
                    Y <strong>{snapshot.uwb.position.y_m.toFixed(2)}</strong> m
                  </span>
                  <span>
                    残差 <strong>{snapshot.uwb.position.residual_m.toFixed(3)}</strong> m
                  </span>
                </div>
              )}
            </>
          ) : (
            <Empty>{telemetry.loading ? '正在读取定位服务…' : '定位服务不可用'}</Empty>
          )}
        </main>
        <aside className="inspector">
          <section>
            <h2>设备连接</h2>
            <p className="quiet">
              已保存接法：
              {snapshot?.config.imu_transport === 'uwb_bridge_v1'
                ? 'UWB 桥接（实验，需自定义固件）'
                : 'IMU 独立 USB'}
            </p>
            {(['uwb', 'imu'] as const).map((kind) => (
              <div className="connection-row" key={kind}>
                <strong>{kind.toUpperCase()}</strong>
                <span
                  className={`signal ${snapshot?.connections[kind].state === 'connected' && !snapshot.connections[kind].stale ? 'online' : ''}`}
                >
                  {kind === 'imu' &&
                  snapshot?.config.imu_transport === 'uwb_bridge_v1' &&
                  snapshot.connections.imu.state === 'connected'
                    ? '共用 A1 串口'
                    : states[snapshot?.connections[kind].state || 'disconnected']}
                  {snapshot?.connections[kind].state === 'connected' &&
                  snapshot.connections[kind].stale
                    ? kind === 'imu' && snapshot.config.imu_transport === 'uwb_bridge_v1'
                      ? ' · 等待有效桥接帧 / 已过期'
                      : ' · 数据过期'
                    : ''}
                  {snapshot?.connections[kind].state === 'connected' &&
                  !snapshot.connections[kind].stale
                    ? ' · 有效数据'
                    : ''}
                </span>
              </div>
            ))}
            {mode === 'device' && (
              <div className="port-fields">
                <label className="field">
                  <span>IMU 接入方式（断开后切换）</span>
                  <select
                    aria-label="IMU 接入方式"
                    value={imuTransport}
                    disabled={action.busy || snapshot?.source_mode !== 'idle'}
                    onChange={(e) =>
                      setImuTransport(e.target.value as TelemetryConfig['imu_transport'])
                    }
                  >
                    <option value="usb">独立 USB 接电脑（现有模式）</option>
                    <option value="uwb_bridge_v1">接标签 STM32，经 UWB（实验 / 需新固件）</option>
                  </select>
                </label>
                {imuTransport === 'uwb_bridge_v1' && (
                  <p className="notice" role="status">
                    方案预接入：球内 IMU → 标签 STM32 → UWB → A1。只填写 A1 串口。 原厂 Location HEX
                    不会传输 IMU；本轮未生成桥接 HEX，切换页面不会更新固件。 未收到自定义 mi v1
                    帧时，IMU 保持无有效数据，不会用定位帧或回放补齐。
                  </p>
                )}
                <label className="field">
                  <span>标签 ID（十进制，默认 5 / 0x05）</span>
                  <input
                    type="number"
                    min="1"
                    max="255"
                    step="1"
                    value={tagId}
                    disabled={action.busy || snapshot?.source_mode !== 'idle'}
                    onChange={(e) => setTagId(Number(e.target.value))}
                  />
                </label>
                <p className="quiet">
                  BP-TWR-30：连接 0x0001 基站，115200 / 8N1。填写运行后端的 Linux 主机串口路径；仅用
                  UWB 时，IMU 留空。桥接模式也只填 A1，但需要配套标签/A1 固件。
                </p>
                <label className="field">
                  <span>
                    {imuTransport === 'uwb_bridge_v1' ? 'A1 共享串口（UWB + IMU）' : 'UWB 串口'}
                  </span>
                  <input
                    value={uwbPort}
                    disabled={action.busy || snapshot?.source_mode !== 'idle'}
                    placeholder="串口路径"
                    onChange={(e) => setUwbPort(e.target.value)}
                  />
                </label>
                {imuTransport === 'usb' && (
                  <label className="field">
                    <span>IMU 串口</span>
                    <input
                      value={imuPort}
                      disabled={action.busy || snapshot?.source_mode !== 'idle'}
                      placeholder="串口路径"
                      onChange={(e) => setImuPort(e.target.value)}
                    />
                  </label>
                )}
                <p className="quiet">
                  Yahboom IMU：115200 / 8N1，7E 23
                  协议；当前仅显示含重力的传感器轴加速度，不显示姿态角。
                </p>
              </div>
            )}
            <button
              className="button secondary full"
              disabled={action.busy || !snapshot}
              onClick={() => {
                if (mode === 'demo') {
                  setMode('device');
                  if (running) void command('demo/stop');
                } else
                  void (snapshot?.source_mode === 'serial' ? command('disconnect') : connect());
              }}
            >
              {snapshot?.source_mode === 'serial' ? '断开串口' : '连接串口'}
            </button>
            {(['uwb', 'imu'] as const).map(
              (kind) =>
                snapshot?.connections[kind].last_error && (
                  <p key={kind} className="error-text">
                    {kind.toUpperCase()}：{snapshot.connections[kind].last_error}
                  </p>
                ),
            )}
            {snapshot &&
              (['uwb', 'imu'] as const).map((kind) => (
                <p key={kind} className="quiet">
                  {kind.toUpperCase()} 累计拒帧：{snapshot[kind].rejected_frames}
                  {snapshot[kind].last_rejection && ` · 最近原因：${snapshot[kind].last_rejection}`}
                </p>
              ))}
            {snapshot?.transport && (
              <p className="quiet">
                A1 分帧拒绝：{snapshot.transport.rejected_frames}
                {snapshot.transport.last_rejection && ` · ${snapshot.transport.last_rejection}`}
              </p>
            )}
          </section>
          <section>
            <h2>锚点坐标</h2>
            <p className="quiet">
              A1 / A2 / A3 对应 0x0001 / 0x0002 /
              0x0003；基站与标签须同高。当前仅三基站二维定位，不测量高度。
            </p>
            <table className="anchor-table">
              <thead>
                <tr>
                  <th>锚点</th>
                  <th>X (m)</th>
                  <th>Y (m)</th>
                </tr>
              </thead>
              <tbody>
                {anchors.map((a, i) => (
                  <tr key={a.id}>
                    <th title={a.id}>A{i + 1}</th>
                    {(['x_m', 'y_m'] as const).map((axis) => (
                      <td key={axis}>
                        <input
                          type="number"
                          step="0.1"
                          aria-label={`A${i + 1} ${axis === 'x_m' ? 'X' : 'Y'}坐标`}
                          value={a[axis]}
                          disabled={action.busy || snapshot?.source_mode !== 'idle'}
                          onChange={(e) =>
                            setAnchors((all) =>
                              all.map((anchor, index) =>
                                index === i
                                  ? { ...anchor, [axis]: Number(e.target.value) }
                                  : anchor,
                              ),
                            )
                          }
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="align-right">
              <button
                className="button secondary small"
                disabled={action.busy || anchors.length !== 3 || snapshot?.source_mode !== 'idle'}
                onClick={() => void save()}
              >
                保存配置
              </button>
            </div>
            {notice && (
              <p className="notice" role="status">
                {notice}
              </p>
            )}
          </section>
          <section className="acceleration-section">
            <div className="section-line">
              <h2>
                加速度 <small>m/s²</small>
              </h2>
              <div className="wave-legend">
                <span>X</span>
                <span>Y</span>
                <span>Z</span>
              </div>
            </div>
            <Waveform history={snapshot?.imu.history || []} />
            <p className="quiet">
              {running
                ? '演示数据 · 传感器坐标 · 含重力'
                : snapshot?.source_mode === 'idle' && snapshot?.imu.history.length
                  ? '历史数据 · 已停止 · 含重力'
                  : '传感器坐标 · 含重力'}
            </p>
            {snapshot?.imu.acceleration && (
              <div className="acceleration-values">
                {(['x', 'y', 'z'] as const).map((axis) => (
                  <span key={axis}>
                    {axis.toUpperCase()} {snapshot.imu.acceleration![axis].toFixed(2)}
                  </span>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
      <Footer>
        {snapshot?.evidence.notice || '设备尚未验收 · 演示回放不代表硬件连接或现场定位精度'}
      </Footer>
    </>
  );
}
