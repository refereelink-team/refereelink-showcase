import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { api } from './api';
import { useResource } from './hooks';
import Multiview from './features/Multiview';
import Experiments from './features/Experiments';
import Tracking from './features/Tracking';
import Telemetry from './features/Telemetry';
import { Alert, Icon } from './components/UI';
const tabs = [
  { id: 'multiview', label: '多视角判罚' },
  { id: 'tracking', label: '场地跟踪' },
  { id: 'foul', label: '实时犯规' },
  { id: 'telemetry', label: '足球定位' },
] as const;
type TabId = (typeof tabs)[number]['id'];
function route(): TabId {
  const id = location.hash.replace(/^#\/?/, '');
  return tabs.some((t) => t.id === id) ? (id as TabId) : 'multiview';
}
export default function App() {
  const [current, setCurrent] = useState<TabId>(route),
    catalog = useResource(api.catalog, 15000),
    nav = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const change = () => setCurrent(route());
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  function navigate(id: TabId) {
    location.hash = `/${id}`;
    setCurrent(id);
  }
  function keyboard(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    navigate(tabs[next].id);
    nav.current?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace-content">
        跳至工作区
      </a>
      <header className="brand-bar">
        <a className="brand" href="#/multiview">
          RefereeLink
        </a>
        <span className="brand-note">足球 · 视觉与运动感知</span>
        <div className="backend-status">
          <span className={`signal ${catalog.data && !catalog.error ? 'online' : ''}`}>
            {catalog.error ? '后台连接中' : catalog.loading ? '连接服务中' : '后台已连接'}
          </span>
          <button
            className="icon-button"
            aria-label="刷新服务状态"
            onClick={() => void catalog.refresh()}
          >
            <Icon name="refresh" size={16} />
          </button>
        </div>
      </header>
      <div className="browser-tabs" ref={nav} role="tablist" aria-label="研究展示页面">
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            className={current === tab.id ? 'selected' : ''}
            role="tab"
            aria-selected={current === tab.id}
            tabIndex={current === tab.id ? 0 : -1}
            aria-controls="workspace-content"
            onClick={() => navigate(tab.id)}
            onKeyDown={(event) => keyboard(event, index)}
          >
            {tab.label}
            <span className="tab-index">0{index + 1}</span>
          </button>
        ))}
      </div>
      <div id="workspace-content" role="tabpanel" aria-labelledby={`tab-${current}`} tabIndex={-1}>
        {catalog.error && (
          <div className="global-alert">
            <Alert
              message={`展示后台暂不可用：${catalog.error}`}
              onRetry={() => void catalog.refresh()}
            />
          </div>
        )}
        {current === 'multiview' ? (
          <Multiview catalog={catalog.data} reload={catalog.refresh} />
        ) : current === 'tracking' ? (
          <Tracking catalog={catalog.data} />
        ) : current === 'telemetry' ? (
          <Telemetry />
        ) : (
          <Experiments key={current} kind={current} catalog={catalog.data} />
        )}
      </div>
    </div>
  );
}
