<#
.SYNOPSIS
    构建多架构镜像（linux/amd64 + linux/arm64）。

.EXAMPLE
    # 推送到仓库（需要先 docker login）
    .\build-multiarch.ps1 -Image myrepo/docker-pull-tar:latest -Push

.EXAMPLE
    # 只在本地生成多架构 OCI 归档（无需仓库）
    .\build-multiarch.ps1 -Image myrepo/docker-pull-tar:latest -OciOutput
#>
param(
    [string]$Image = "dockerpulltar/app:latest",
    [string]$Platforms = "linux/amd64,linux/arm64",
    [string]$Builder = "dockerpulltar-builder",
    [switch]$Push,
    [switch]$OciOutput
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 docker 命令，请先安装 Docker Desktop。"
}

Write-Host "==> 检查 buildx"
docker buildx version

docker buildx inspect $Builder *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> 创建 builder: $Builder"
    docker buildx create --name $Builder --driver docker-container --use
} else {
    Write-Host "==> 复用 builder: $Builder"
    docker buildx use $Builder
}
docker buildx inspect --bootstrap

$common = @("buildx", "build", "--platform", $Platforms, "-t", $Image)

if ($Push) {
    Write-Host "==> 构建并推送多架构镜像: $Image"
    & docker @common --push .
} elseif ($OciOutput) {
    $dest = Join-Path (Get-Location) "docker-pull-tar-multiarch.oci.tar"
    Write-Host "==> 构建本地多架构 OCI 归档: $dest"
    & docker @common --output "type=oci,dest=$dest" .
} else {
    Write-Host "==> 构建多架构镜像（默认使用 --push 输出到镜像仓库）"
    Write-Host "    如需推送请加 -Push；如需本地归档请加 -OciOutput"
    & docker @common --push .
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "构建失败"
}

Write-Host "==> 完成。可用以下命令验证多架构清单："
Write-Host "    docker buildx imagetools inspect $Image"
