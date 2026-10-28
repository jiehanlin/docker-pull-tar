# Docker Image Puller

## 项目简介

Docker Image Puller 是一个方便的工具，用于从 Docker 仓库拉取镜像，支持断点续传、多架构选择。该工具采用 MIT 许可证，开放源代码，方便用户根据需要进行定制和扩展。

## 特点

- **无需安装 Docker 或 Python 环境**：直接使用单文件 EXE 或 Python 脚本，开箱即用。
- **无依赖 EXE 执行**：编译为独立 EXE 文件，无需安装 Python 环境，无需安装 Docker 环境，直接在 Releases 下载就能直接使用。
- **断点续传**：支持下载中断后继续下载，无需重新开始。
- **失败重试**：自动重试失败的下载，最大重试10次，确保下载成功。
- **SHA256 校验**：下载完成后自动校验文件完整性，确保镜像正确。
- **多架构支持**：支持多种架构（如 `amd64`、`arm64`），自动识别镜像可用架构并提示选择。
- **兼容最新 Docker Registry API**：确保与 Docker Hub、Quay.io 等镜像仓库的最新接口兼容。
- **单文件 Python 脚本**：便于携带和使用，无需复杂安装。
- **用户友好**：提供交互式输入，简化操作流程。
- **下载统计**：显示平均下载速度和总耗时。
- **自定义输出目录**：支持指定下载目录，默认输出到当前目录。

## 🌟 全新 Web 可视化版本 (Gradio UI)

除了命令行工具，本次更新特别推出了基于 Gradio 的一站式 Web 图形界面版本（`app.py`），让搜索、下载、配置和管理流程彻底可视化，操作体验跨越式升级！无需记忆任何命令行参数，只需点点鼠标即可完成离线打包。

### Web 版核心亮点：

- 🖥️ **一站式 Web 控制台**：告别黑框框，启动后直接在浏览器中进行全流程交互。
- 🔍 **全网可视搜索与表格展示**：输入关键词，实时呈现包含**镜像名称、Star 数、拉取量、更新日期**等详细信息的直观表格。
- 🖱️ **智能联动选择**：在左侧表格中点击任意镜像，右侧会自动拉取所有可用 Tag 列表。Tag 与架构（amd64/arm64等）均可通过下拉框轻松点选。
- 🌐 **多源切换 & 图形化代理配置**：
  - 内置 **华为云 DDN 加速（docker.io 国内中转）**、1ms 专属加速源、DaoCloud、轩辕、Docker 官方源一键切换；所选源不可用时会**自动回退**到下一个源。
  - **镜像源可配置**：在「⚙️ 全局设置项 → 🧩 镜像源配置」中直接增删改源，每行格式 `名称|registry 主机名|仓库路径前缀(可选)`，顺序即优先级；可「保存到配置文件」持久化到 `conf/sources.conf`。
  - 支持直接粘贴**完整镜像地址**，例如 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.31.8`，自动识别 registry 与仓库路径。
  - Tag 列表直接来自 registry 的 `tags/list` 实时接口，并按版本倒序排列（最新版本置顶），刚发布的版本（如 `nginx:1.31.8`）也能立刻选到，不再受搜索索引滞后影响。
  - 搜索框支持 `nginx:1.31.8`、`docker.io/nginx:1.31.8` 等写法，`docker.io/` 等前缀会被自动归一化。
  - 提供单选按钮轻松切换网络模式（无代理、系统代理、自定义账号密码代理），并支持一键开关 SSL 验证。
- 📊 **精美实时进度面板**：消除传统命令行的刷屏闪烁，提供定制化的 HTML 动态多排条形进度UI，实时展示：当前分块状态、总进度百分比、各分层下载量，以及**预估实时下载速度**。
- 📁 **内置本地镜像大管家**：提供独立的本地 `.tar` 文件管理面板。在前端不仅可以看到所有已下载的包，还支持**直接在浏览器里点击下载到当前设备**，以及提供下拉框一键删除不需要的包以释放空间。

### Web 版使用方法：

**运行环境准备（如果非 EXE 版本）：**
```bash
# 确保安装了相关依赖包
pip install -r requirements.txt
```

**启动服务：**
```bash
python app.py
```
终端提示 `服务启动中...` 后，在任意浏览器中打开 `http://127.0.0.1:7860` 即可开始使用。

### Web 版操作三步走：
1. **第一步（查）**：输入 `nginx` 等关键词，点击搜索，表格出结果后点击你想要的那一行。
2. **第二步（选）**：右侧确认 Tag（版本）和 Arch（架构），点击“开始下载”。
3. **第三步（管）**：下方进度条跑满后，自动在本地镜像列表中刷出 `.tar` 文件，可以直接下载到本机转移或一键删除。

## 🐳 Docker 化部署（同时支持 amd64 / arm64）

### 1. 单架构快速构建

```bash
docker build -t dockerpulltar/app:local .
docker run -d --name docker-pull-tar -p 7860:7860 \
  -v "$(pwd)/downloads:/app/downloads" \
  -v "$(pwd)/conf:/app/conf" \
  dockerpulltar/app:local
```

访问 http://127.0.0.1:7860 即可。

### 2. docker compose 一键启动

```bash
docker compose up -d          # 后台启动
docker compose logs -f        # 查看日志
docker compose down           # 停止
```

### 3. 多架构镜像（linux/amd64 + linux/arm64）

一个镜像同时适配两种主机，**由 buildx 构建多架构清单（manifest list）**：

```bash
# Linux / macOS
./build-multiarch.sh myrepo/docker-pull-tar:latest --push

# Windows PowerShell
.\build-multiarch.ps1 -Image myrepo/docker-pull-tar:latest -Push
```

手动等价命令：

```bash
docker buildx create --name dockerpulltar-builder --driver docker-container --use
docker buildx inspect --bootstrap
docker buildx build --platform linux/amd64,linux/arm64 \
    -t myrepo/docker-pull-tar:latest --push .
```

验证多架构清单：

```bash
docker buildx imagetools inspect myrepo/docker-pull-tar:latest
```

拉取时由 Docker 自动按宿主机架构选择对应镜像：

```bash
docker pull myrepo/docker-pull-tar:latest
```

> 无镜像仓库时可用 `-OciOutput`（PowerShell）/ `--oci`（Shell）导出本地多架构 OCI 归档
> `docker-pull-tar-multiarch.oci.tar`。
>
> 在 amd64 机器上跑 arm64 镜像需安装 QEMU 模拟层：
> `docker run --privileged --rm tonistiigi/binfmt --install arm64`
>
> 权限提示：镜像内以非 root（uid 10001）运行，若宿主机 `./downloads` 目录不可写，
> 会导致下载保存失败。执行 `chmod 777 downloads`，或在 compose 中取消 `user:` 注释
> 以宿主机用户身份运行。

### 4. 容器内可用环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GRADIO_SERVER_NAME` | `0.0.0.0` | 监听地址，容器内必须为 `0.0.0.0` |
| `GRADIO_SERVER_PORT` | `7860` | 服务端口 |
| `GRADIO_INBROWSER` | `0`（容器） | 是否自动打开浏览器 |
| `GRADIO_SHARE` | `0` | 是否生成公网临时链接 |
| `SOURCES_CONFIG` | `conf/sources.conf` | 镜像源配置文件路径（容器内 `/app/conf/sources.conf`） |
| `PIP_INDEX_URL` | 清华镜像 | 构建参数，指定 pip 源 |

### 5. 一次导出多个架构

界面中的「选择架构 Arch」为**多选**，勾选 `amd64` + `arm64` 后点一次下载，会依次生成两个独立包：

```
downloads/library_nginx_1.31.6_amd64.tar
downloads/library_nginx_1.31.6_arm64.tar
```

在目标机上分别导入：

```bash
docker load -i library_nginx_1.31.6_amd64.tar
docker load -i library_nginx_1.31.6_arm64.tar
```

### 6. WSL Container Desktop（`wslc.exe`，Windows 11 + WSL 2.9.3+ 自带）

无需安装 Docker Desktop，WSL 自带容器 CLI：

```powershell
# 构建（基础镜像默认走华为云 DDN 加速源）
wslc build -t dockerpulltar/app:local .

# 运行：容器内 7860 映射到宿主 7860
# 把 <你的项目目录> 换成自己的本地路径，例如 D:\code\docker-pull-tar
wslc run -d --name docker-pull-tar -p 7860:7860 `
  -v "<你的项目目录>\downloads:/app/downloads" `
  -v "<你的项目目录>\conf:/app/conf" `
  dockerpulltar/app:local

# 查看
wslc container ps
wslc logs docker-pull-tar
wslc image ls
```

镜像源配置：
- 界面编辑：首页「⚙️ 全局设置项 → 🧩 镜像源配置」，每行 `名称|registry 主机名|仓库路径前缀(可选)`，顺序即优先级。
- 持久化：点「💾 保存到配置文件」写入 `conf/sources.conf`（容器内 `/app/conf/sources.conf`）。
- 挂载 `conf` 目录后，在宿主机直接改 `conf/sources.conf` 并重启容器即可换源；也可用环境变量 `SOURCES_CONFIG` 指定其它路径。

注意事项：

- **端口**：容器内固定监听 7860（`GRADIO_SERVER_PORT`），映射 `-p 7860:7860` 后浏览器访问 `http://127.0.0.1:7860` 即可。
- **卷挂载必须写 Windows 路径**（如 `D:\data\downloads`）。容器命名空间内没有 `/mnt/f`，写 `/mnt/f/...` 会挂出一个空目录（不报错，容易误判成功）。
- **只能挂目录，不能挂单个文件**：`-v "D:\conf\sources.conf:/app/conf/sources.conf"` 会直接让 wslc 引擎崩溃（报 `ERROR_UNHANDLED_EXCEPTION`，容器起不来）。要挂配置文件请挂它所在的目录。
- `wslc` **没有 `--platform` 参数**，只能构建宿主架构（amd64）的镜像；arm64 镜像需在 arm64 机器上构建。
- 构建上下文里的 `# syntax=docker/dockerfile:1` 会触发拉取 `docker/dockerfile:1` 前端镜像，Docker Hub 不可达时会直接失败，本项目 Dockerfile 已不声明该行。
- 基础镜像默认走华为云 DDN（docker.io 国内中转）：
  `ARG BASE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.13-slim-bookworm`，
  需要 1ms 源或官方源时用 `--build-arg BASE_IMAGE=docker.1ms.run/library/python:3.13-slim-bookworm`
  （或 `--build-arg BASE_IMAGE=python:3.13-slim-bookworm`）覆盖。

## Web服务截图

![使用截图](./screenshots/1ms_web_screenshots_1.png)
![使用截图](./screenshots/1ms_web_screenshots_2.png)

## 命令截图：

![用户界面截图](./screenshots/截图.jpg)

### 1ms 专版

1ms 专版是针对 **1ms.run** 镜像仓库优化的版本，提供关键词搜索和一键下载功能。

#### 特点

- **关键词搜索**：无需知道完整镜像名，输入关键词即可搜索相关镜像
- **智能推荐**：显示搜索结果中每个镜像的下载量stars，帮助选择最流行的镜像
- **tag 选择**：自动获取并展示可用 tag 列表
- **多架构支持**：自动识别可用架构并支持选择
- **多线程下载**：支持并发下载，加速大镜像拉取
- **无需 Docker 环境**：生成 .tar 包，方便内网传输和使用

#### 使用方法

```bash
python docker_image_puller_1ms.py
```

#### 操作流程

1. **搜索镜像**：输入关键词搜索 Docker 镜像

   ![搜索镜像](./screenshots/1ms-1.搜索镜像.png)

2. **选择标签**：从可用 tag 列表中选择需要的版本

   ![选择标签](./screenshots/1ms-2.选择标签tag.png)

3. **选择架构**：根据目标设备选择合适的 CPU 架构

   ![选择架构](./screenshots/1ms-3.选择架构.png)

4. **多线程下载**：支持并发下载，显示实时进度

   ![多线程下载](./screenshots/1ms-4.多线程下载.png)

#### 参数说明

| 参数 | 说明 |
|------|------|
| `-k, --keyword` | 搜索关键词 |
| `-i, --image` | 镜像名称（与 --keyword 二选一） |
| `-t, --tag` | 镜像 tag（不传则从列表中选择） |
| `-a, --arch` | 架构，默认：amd64 |
| `-o, --output` | 输出目录 |
| `-w, --workers` | 并发下载线程数，默认4 |
| `--api` | 1ms API 地址，默认：https://1ms.run/api/v1/registry |
| `--registry` | 1ms registry 地址，默认：docker.1ms.run |

#### 示例

```bash
# 交互式搜索下载
python docker_image_puller_1ms.py

# 指定关键词搜索
python docker_image_puller_1ms.py -k nginx

# 指定镜像和tag
python docker_image_puller_1ms.py -i nginx -t latest -a arm64
```

---

## 安装

### 下载 EXE 文件

前往 [Releases](https://github.com/topcss/docker-pull-tar/releases) 页面，下载 `DockerPull.exe`，无需安装任何依赖，直接运行。

### 通过 Git 克隆

```bash
git clone https://github.com/topcss/docker-pull-tar.git
```

## 使用方法

### 基本用法

```bash
DockerPull.exe [选项]
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-i, --image` | Docker 镜像名称（例如：nginx:latest 或 harbor.abc.com/abc/nginx:1.26.0） |
| `-a, --arch` | 架构，默认：amd64，常见：amd64, arm64 等 |
| `-r, --custom-registry` | 自定义仓库地址（例如：harbor.abc.com） |
| `-u, --username` | Docker 仓库用户名 |
| `-p, --password` | Docker 仓库密码 |
| `-o, --output` | 输出目录，默认为当前目录下的镜像名_tag_arch 目录 |
| `-q, --quiet` | 静默模式，减少交互 |
| `--debug` | 启用调试模式，打印详细日志 |
| `--workers` | 并发下载线程数，默认4 |
| `-v, --version` | 显示版本信息 |
| `-h, --help` | 显示帮助信息 |

### 示例

#### 交互式模式

```bash
> DockerPull.exe

🚀 Docker 镜像拉取工具 v1.4.0
请输入 Docker 镜像名称（例如：nginx:latest 或 harbor.abc.com/abc/nginx:1.26.0）：alpine
请输入自定义仓库地址（默认 dockerhub）：
请输入镜像仓库用户名：
请输入镜像仓库密码：
📋 当前可用架构：amd64, arm64, armv7, ppc64le, s390x
请输入架构（可选: amd64, arm64, armv7, ppc64le, s390x，默认: amd64）：arm64
📦 仓库地址：registry-1.docker.io
📦 镜像：library/alpine
📦 标签：latest
📦 架构：arm64
📁 输出目录：library_alpine_latest_arm64
📥 开始下载...
✅ 镜像已保存为: library_alpine_latest_arm64.tar
💡 导入命令: docker load -i library_alpine_latest_arm64.tar
```

#### 命令行模式

```bash
# 下载 Docker Hub 镜像
DockerPull.exe -i nginx:latest

# 下载指定架构镜像
DockerPull.exe -i alpine:latest -a arm64

# 下载私有仓库镜像
DockerPull.exe -i harbor.example.com/library/nginx:1.26.0 -u admin -p password

# 指定输出目录
DockerPull.exe -i nginx:latest -o ./downloads

# 静默模式下载
DockerPull.exe -i nginx:latest -q

# 下载 Quay.io 多架构镜像
DockerPull.exe -i quay.io/ascend/vllm-ascend:v0.11.0-a3-openeuler -a arm64
```

## 输出目录说明

工具默认将镜像下载到当前目录下的 `镜像名_tag_arch` 目录中，例如：
- `library_alpine_latest_amd64/`
- `ascend_vllm-ascend_v0.11.0-a3-openeuler_arm64/`

可以使用 `-o` 参数指定输出目录：
```bash
DockerPull.exe -i nginx:latest -o ./downloads
# 输出到 ./downloads/library_nginx_latest_amd64/
```

## 内网 Docker 导入方法

1. **拉取镜像并打包**  
   使用本工具拉取镜像并生成 `.tar` 文件，例如 `library_alpine_latest_amd64.tar`。

2. **将 `.tar` 文件传输到内网机器**  
   通过 U 盘、内网文件服务器或其他方式将 `.tar` 文件传输到目标机器。

3. **导入镜像到 Docker**  
   在内网机器上运行以下命令导入镜像：

   ```bash
   docker load -i library_alpine_latest_amd64.tar
   ```

4. **验证镜像**  
   导入完成后，运行以下命令查看镜像：

   ```bash
   docker images
   ```

   然后启动容器：

   ```bash
   docker run -it alpine
   ```

## 高可用性特性

### 断点续传

- 下载中断后，再次运行相同命令会自动从断点继续下载
- 支持网络中断、程序崩溃等场景的恢复
- 进度文件保存在输出目录中

### 失败重试

- 认证失败：最多重试3次
- 清单获取失败：最多重试3次
- 文件下载失败：最多重试10次
- 采用指数退避策略，避免服务器压力过大

### SHA256 校验

- 每个下载的层都会进行 SHA256 校验
- 校验失败自动删除损坏文件并重新下载

### 信号处理

- 支持 Ctrl+C 优雅退出
- 退出时自动保存下载进度

## 许可证

本项目采用 MIT 许可证，详情见 [LICENSE](LICENSE) 文件。

## 联系方式

如有任何问题或建议，请通过 [GitHub Issues](https://github.com/topcss/docker-pull-tar/issues) 提出。

## 为什么选择这个工具？

- **无需安装 Docker 或 Python**：直接运行 EXE 文件，适合内网环境。
- **断点续传**：网络中断不怕，继续下载即可。
- **高可靠性**：自动重试、校验完整性，确保下载成功。
- **架构灵活**：支持 `amd64` 和 `arm64` 等架构，适应多种环境。
- **易于使用**：单文件脚本，无需复杂配置。
- **开放源代码**：自由定制和扩展。

## 常见问题

**Q**: 如何配置国内镜像源？  
**A**: 使用 `-r` 参数指定仓库地址，或设置环境变量 `HTTP_PROXY` / `HTTPS_PROXY`。

**Q**: 支持哪些架构？  
**A**: 支持 Docker Hub 上所有 Linux 架构，常见：`amd64`、`arm64`、`armv7` 等。工具会自动列出可用架构供选择。

**Q**: 是否需要安装 Docker 或 Python？  
**A**: 不需要！直接下载 `DockerPull.exe` 即可运行。

**Q**: 如何在内网中使用？  
**A**: 使用本工具拉取镜像并生成 `.tar` 文件，然后通过 `docker load` 命令导入内网机器。

**Q**: 下载中断了怎么办？  
**A**: 直接再次运行相同命令，工具会自动从断点继续下载。

**Q**: 如何指定下载目录？  
**A**: 使用 `-o` 参数指定，例如 `DockerPull.exe -i nginx:latest -o ./downloads`。

**Q**: 提示“镜像或标签不存在 / 404”怎么办？  
**A**: 三种常见原因：① 输入带了 `docker.io/` 前缀 —— 现在已自动归一化为 `library/nginx`；② Tag 下拉框数据陈旧 —— 现在改为直连 registry 的 `tags/list` 实时接口并按版本倒序，新版本立即可见；③ 所选镜像源缓存滞后 —— Web 版会自动回退到下一个源（华为云 DDN → 1ms → DaoCloud → 轩辕 → 官方），也可在下拉框手动切换源。

**Q**: Docker Hub 官方源太慢/超时，用哪个源？  
**A**: 默认首选就是**华为云 DDN 加速**（`swr.cn-north-4.myhuaweicloud.com`，docker.io 的国内中转），例如 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.31.8`。注意该代理源按架构拆分 tag：arm64 变体形如 `1.31.8-linuxarm64`，且同步较慢；工具会自动尝试变体，代理源缺该架构时自动回退到 1ms 等多架构源。

---

鸣谢：topcss Jack、Potterluo Keriko、wang-lg 等网友贡献的代码。

希望通过这个工具能为您的 Docker 镜像管理带来便利！ 🚀

