#
# Docker 镜像离线拉取工具（Web / Gradio 版）
# 该镜像同时支持 linux/amd64 与 linux/arm64，可通过 buildx 构建多架构镜像。
#
# 构建（单架构）：
#   docker build -t dockerpulltar/app:local .
#
# 构建（多架构 linux/amd64,linux/arm64）：
#   docker buildx build --platform linux/amd64,linux/arm64 -t <你的仓库>/app:latest --push .
#
# 基础镜像走国内可达的加速源；若需官方源：
#   --build-arg BASE_IMAGE=python:3.13-slim-bookworm
#
# 默认用华为云 DDN（docker.io 国内中转）：1ms 源的 blob 会被重定向到
# cloudfront-docker-cf.mrs.1ms.run，实测会出现长时间无数据的读超时，
# 使 wslc build 卡在 extracting 阶段不返回；DDN 实测 3 MB/s 稳定。
ARG BASE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.13-slim-bookworm
FROM ${BASE_IMAGE}

# 国内可用 pip 镜像，构建时可用 --build-arg PIP_INDEX_URL=... 覆盖
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    GRADIO_INBROWSER=0 \
    GRADIO_ANALYTICS_ENABLED=False \
    GRADIO_TEMP_DIR=/tmp/gradio \
    HF_HOME=/tmp/hf \
    TZ=Asia/Shanghai

WORKDIR /app

# 镜像源配置目录（可挂载宿主机目录以持久化界面里保存的源配置）
ENV SOURCES_CONFIG=/app/conf/sources.conf

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY app.py ./

# 非 root 运行，并预创建下载目录与配置目录（挂载为卷）
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/downloads /app/conf /tmp/gradio \
    && chown -R appuser:appuser /app /tmp/gradio
USER appuser

VOLUME ["/app/downloads"]
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/', timeout=4).status == 200 else 1)"

CMD ["python", "app.py"]
