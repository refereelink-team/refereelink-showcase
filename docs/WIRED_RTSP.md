# 现场输入：手机有线 RTSP

Showcase 的现场输入继续读取 Backend 的真实设备状态和预览。手机身份仍为 `field`，另用 `video_transport` 区分 RTSP/SRT；仅当 `network_type` 为实际 `wiredEthernet` 时显示“有线 RTSP”。只有最近收到可解码图片才显示正在收流。

先启动 Mac Backend 的 `bash tools/start_offline_mac.sh`（其中管理 MediaMTX），再在 Field App 配置 Mac 的局域网地址。手机、两台摄像头、Mac 接同一交换机。手机以太网 RTSP 视频和 REST/WebSocket 位姿/控制使用同一 Mac，断线后本地录制继续。

开发启动示例：

```sh
BACKEND_URL=http://<CUDA-host>:8000 LIVE_BACKEND_URL=http://127.0.0.1:8005 npm run dev
```

`LIVE_BACKEND_URL` 只代理 `/api/multiview/live` 路径及子路径。模型、案例和其他 API 仍用 `BACKEND_URL`。不设置时延续原统一代理行为；生产部署须配置相应反向代理，Vite 开发变量不替代生产网关。

预览持续刷新（约 2 fps），单请求在途、超时与对象 URL 清理防止旧图和请求堆积；状态每秒更新，隐藏页暂停图片请求。预览刷新率与原始录像帧率不同。

保留现有精选实地/视频案例与切换清空判罚行为。本 PR 不同步 Backend 代码或视频。软件验证包括类型检查、测试、格式检查、构建及浏览器实际操作本地收流和停止。生成测试画面不代表物理手机以太网或真实摄像头三路验收；接线未完成，这部分待验收。
