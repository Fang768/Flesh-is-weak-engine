# WSL2 + Docker 迁移手册（Linux 跑 QQ，规避设备级外挂检测）

> 目标：把 SnowLuma/NapCat 从 Windows 注入搬到 Linux 容器里跑，避免腾讯「设备存在外挂软件」判定。
> 前提：**必须换一个干净小号**（Linux 只解决设备残留，不解决同一公网 IP 关联）。

## 现状（2026-11-06 14:59）

| 项 | 状态 |
|---|---|
| `Microsoft-Windows-Subsystem-Linux` | **Enabled**（dism exit=3010，待重启） |
| `VirtualMachinePlatform` | **Enabled**（同上） |
| 重启 | **未做**（不重启 WSL 不可用） |
| WSL 内核包 | 已下载 `deploy/payloads/wsl_update_x64.msi`（17,104,896 B） |
| 发行版 rootfs | 已下载 `deploy/payloads/alpine-minirootfs-3.21.0-x86_64.tar.gz`（3,509,360 B） |
| 编排文件 | 已写 `deploy/Dockerfile`、`deploy/docker-compose.yml`、`deploy/requirements.txt` |
| 网络限制 | github.com 直连不可达；`api.github.com` 可达；`dl-cdn.alpinelinux.org`、`wslstorestorage.blob.core.windows.net` 可达 |

## 重启之后按顺序执行

### 1) 装 WSL 内核（管理员）

```powershell
msiexec /i D:\Code\Synthetic_Dawn\nexus-bot\deploy\payloads\wsl_update_x64.msi /qn
```

### 2) 设为 WSL2 并导入 Alpine

```powershell
wsl --set-default-version 2
wsl --import nexus-alpine D:\WSL\nexus-alpine D:\Code\Synthetic_Dawn\nexus-bot\deploy\payloads\alpine-minirootfs-3.21.0-x86_64.tar.gz
wsl -d nexus-alpine -e sh -c "cat /etc/os-release | head -3"
```

如果 `wsl --import` 报版本不支持，说明 inbox 版 WSL 太老 → 走备用方案（见文末）。

### 3) 先把 Alpine 的源换成国内

```sh
wsl -d nexus-alpine -u root -e sh -c "sed -i 's|dl-cdn.alpinelinux.org|mirrors.aliyun.com|g' /etc/apk/repositories && apk update"
```

### 4) 装 Docker

```sh
wsl -d nexus-alpine -u root -e sh -c "apk add docker docker-cli-compose openrc && rc-update add docker && rc-service docker start && docker version"
```

> Alpine 用 OpenRC；若 `rc-service docker start` 失败，用 `setsid dockerd > /var/log/dockerd.log 2>&1 &` 手动起。

### 5) 起 napcat + nonebot

把 `deploy/` 目录挂进 WSL（或是直接从 `/mnt/d/...` 访问），然后：

```sh
wsl -d nexus-alpine -u root -e sh -c "cd /mnt/d/Code/Synthetic_Dawn/nexus-bot/deploy && docker compose up -d"
```

注意：容器里要能访问宿主 `127.0.0.1:8080`。WSL2 里 `localhost` 会转发到 Windows 宿主，所以 nonebot 若仍跑在 Windows，napcat 容器用 `ws://host.docker.internal:8080/onebot/v11/ws`（compose 里已加 `extra_hosts: host.docker.internal:host-gateway`）。

### 6) 登录

```sh
wsl -d nexus-alpine -u root -e sh -c "docker logs -f napcat"
```

扫日志里的二维码，用**新小号**登录。登录后 NapCat WebUI 在 Windows 侧 `http://127.0.0.1:6099`。

## 备用方案（inbox WSL 拒绝导入时）

`api.github.com` 可达但 `github.com` 不可达，所以：

1. 从 `https://api.github.com/repos/microsoft/WSL/releases/latest` 拿 `wsl.3.0.3.0.x64.msi`（350MB）的真实下载地址；
2. 该地址是 `github.com/...`，需要 release-assets 直链或代理（之前验证：`edgeone.gh-proxy.com` 可用但约 13KB/s → 350MB 需要 7 小时，不现实）；
3. 因此若 WSL 路线彻底卡住，**改租 VPS**：2C2G，把 `deploy/` 里的 compose 丢上去，10 分钟起。

## 回滚

```powershell
# 管理员
dism /online /disable-feature /featurename:VirtualMachinePlatform /norestart
dism /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart
wsl --unregister nexus-alpine
```
