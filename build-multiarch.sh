#!/usr/bin/env bash
# 构建多架构镜像（linux/amd64 + linux/arm64）
#
# 用法：
#   ./build-multiarch.sh myrepo/docker-pull-tar:latest --push
#   ./build-multiarch.sh myrepo/docker-pull-tar:latest --oci
set -euo pipefail

IMAGE="${1:-dockerpulltar/app:latest}"
MODE="${2:---push}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER="${BUILDER:-dockerpulltar-builder}"

command -v docker >/dev/null 2>&1 || { echo "未找到 docker 命令"; exit 1; }

echo "==> 检查 buildx"
docker buildx version

if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    echo "==> 创建 builder: $BUILDER"
    docker buildx create --name "$BUILDER" --driver docker-container --use
else
    echo "==> 复用 builder: $BUILDER"
    docker buildx use "$BUILDER"
fi
docker buildx inspect --bootstrap

case "$MODE" in
    --push)
        echo "==> 构建并推送: $IMAGE"
        docker buildx build --platform "$PLATFORMS" -t "$IMAGE" --push .
        ;;
    --oci)
        DEST="./docker-pull-tar-multiarch.oci.tar"
        echo "==> 构建本地多架构 OCI 归档: $DEST"
        docker buildx build --platform "$PLATFORMS" -t "$IMAGE" --output "type=oci,dest=$DEST" .
        ;;
    *)
        echo "用法: $0 <image:tag> [--push|--oci]"
        exit 2
        ;;
esac

echo "==> 验证多架构清单:"
docker buildx imagetools inspect "$IMAGE" || true
