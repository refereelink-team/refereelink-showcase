# 多视角案例视频：下载与解压

Git 只保存代码、案例目录和校验信息。视频和视频压缩包上传到 Google Drive，不进入 Git。

## 本次视频包

- [下载视频 ZIP](https://drive.google.com/file/d/1XZykhk8AFMNFmsSTIRmOJxMZdipwOO0B/view?usp=drivesdk)
- [Google Drive 归档目录](https://drive.google.com/drive/folders/1Tw3zyGxrFrhgXoQMgfxc5XYEzfoM3eJQ)
- 文件名：`refereelink-showcase-field-cases-20261005-v1.zip`
- 大小：53,901,226 字节，约 53.9 MB。
- 内容：**原编号实地测试案例1–6**，每个案例两条视频，共 **12 条视频**，以及 6 份裁切、时间戳与同步来源记录。
- 不含整场原片、自动生成的候选、模型权重或历史判罚。原有 **视频测试案例1–5** 继续使用已配置上游服务的素材，不在本次新增视频包内。

## 放置与解压：一个目录、一条命令

在需要恢复视频的 **refereelink-showcase 仓库根目录** 创建下载目录：

```sh
mkdir -p runtime/packages
```

从上面的 Drive 链接下载 ZIP，保持文件名不变，放到 `runtime/packages/`。然后仍在仓库根目录执行：

```sh
unzip -o runtime/packages/refereelink-showcase-field-cases-20261005-v1.zip
```

压缩包已经包含正确的相对路径，**不用再移动视频或改名**：

```text
runtime/media/multiview/
  field-test-01/{camera-1.mp4,camera-2.mp4,provenance.json}
  field-test-02/{camera-1.mp4,camera-2.mp4,provenance.json}
  field-test-03/{camera-1.mp4,camera-2.mp4,provenance.json}
  field-test-04/{camera-1.mp4,camera-2.mp4,provenance.json}
  field-test-05/{camera-1.mp4,camera-2.mp4,provenance.json}
  field-test-06/{camera-1.mp4,camera-2.mp4,provenance.json}
```

前端目前仅展示原案例 1、2、5，并重新编号为实地测试案例 1、2、3。因此网页案例 1、2、3 分别读取 `field-test-01`、`field-test-02`、`field-test-05`。原案例 3、4、6 仅在前端隐藏，完整 ZIP 仍保留六组视频。案例名称和机位同步偏移在 [`cases/multiview-field-tests.json`](../cases/multiview-field-tests.json)；文件大小、SHA-256 和 Drive 位置在 [`cases/multiview-field-tests-asset-bundle.json`](../cases/multiview-field-tests-asset-bundle.json)。

Mac 的网页通过 `BACKEND_URL` 读取远端 API 和视频。恢复远端素材时，请在 **CUDA 主机的 showcase 仓库根目录** 执行同一条解压命令；仅在 Mac 解压不会改变远端服务使用的文件。当前已配置的上游案例服务继续提供案例目录与推理。这个 ZIP 恢复媒体文件，不安装模型或注册新的上游服务；新部署时应让上游案例目录按 showcase 仓库根目录解析上述相对路径。无需将 Backend 源码复制进 showcase。

## 可选：核对文件完整性

在仓库根目录运行，18 个文件都应显示 `OK`：

```sh
shasum -a 256 -c cases/multiview-field-tests.sha256
```

ZIP 自身的 SHA-256：

```text
8c2b3818a253b6ec5e4bc287da3f8e6c076bab7560f6bd5aa5c787bd3589b3b0
```

已在空目录实际解压并核对全部文件，上传后回读 Drive 元数据确认文件名、大小和所属目录。
