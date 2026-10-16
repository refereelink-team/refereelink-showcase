# 现场输入：手机有线 RTSP

Showcase 的现场输入继续读取 Backend 的真实设备状态和预览。手机身份仍为 `field`，另用 `video_transport` 区分 RTSP/SRT；仅当 `network_type` 为实际 `wiredEthernet` 时显示“有线 RTSP”。只有最近收到可解码图片才显示正在收流。

默认拓扑现为 [单 PC / WSL2 本地 CUDA](LOCAL_WSL.md)：Showcase 在 WSL 的 8010 端口，完整 Backend 的现场接收、MediaMTX 与多视角服务需另外部署在同机，默认上游为 8000。此仓库不包含这些接收服务，也不会自动启动或迁移它们。手机、两台摄像头与这台 PC 接同一交换机；Field App 配置实际接收服务的可达局域网地址，不能配置只供本机浏览器使用的 `127.0.0.1`。

WSL 的本机 localhost 转发不等于外部手机可访问 WSL。接收服务的 LAN 监听、WSL 网络模式及所需端口的防火墙授权须在该 PC 上单独验证，不要为此关闭整个防火墙。手机以太网 RTSP 视频和 REST/WebSocket 位姿/控制应使用同一接收端，断线后本地录制继续。旧 Mac 的 `tools/start_offline_mac.sh` 属于另一个 Backend 的历史部署，不是当前 WSL 启动入口。

开发启动示例：

```sh
npm run start:backend
# 第二个 WSL 终端：
npm run dev
```

默认所有请求由 Vite 代理到本机 Showcase 8010，再由其多视角网关访问本机上游 8000。可选的 `LIVE_BACKEND_URL` 仍只代理 `/api/multiview/live` 路径及子路径，其余 API 用 `BACKEND_URL`；这是显式分离部署的开发选项，单 PC 默认不需要。删除旧的云端或 Mac 地址覆盖并重启 Vite。生产构建由 8010 直接提供，Vite 开发变量不替代生产网关。

预览持续刷新（约 2 fps），单请求在途、超时与对象 URL 清理防止旧图和请求堆积；状态每秒更新，隐藏页暂停图片请求。预览刷新率与原始录像帧率不同。

保留现有精选实地/视频案例与切换清空判罚行为。本仓库不同步完整 Backend 代码或视频。历史软件验证包括类型检查、测试、格式检查、构建及浏览器操作本地收流和停止；这些记录不构成当前 WSL PC 的验收。生成测试画面不代表物理手机以太网或真实摄像头三路验收，这部分仍待在实际 PC 完成。
