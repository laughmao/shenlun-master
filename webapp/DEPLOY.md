# 云服务器部署指南（Docker）

架构：代码走 GitHub（不含任何密钥），服务器上用 Docker 构建，Key 只存在于
服务器上的 `webapp/.env`（只读挂载进容器，不进镜像、不进 git）。

## 首次部署

```bash
# 1. 服务器上装 Docker（以 Ubuntu/Debian 为例）
curl -fsSL https://get.docker.com | sh

# 2. 拉代码
git clone https://github.com/liuyuexi1987/shenlun-review-pro.git
cd shenlun-review-pro

# 3. 创建 Key 文件（唯一放 Key 的地方，不进 git、不进镜像）
cd webapp
cat > .env <<'EOF'
SHENLUN_DEFAULT_BASE_URL=https://你的中转地址
SHENLUN_DEFAULT_API_KEY=sk-xxxx
SHENLUN_DEFAULT_MODEL=你的模型名
EOF
chmod 600 .env
cd ..

# 4. 构建并启动（默认走清华 PyPI 镜像，国内服务器构建不卡）
docker compose up -d --build

# 5. 验证
curl http://127.0.0.1:8787/api/health
# 期望返回 {"ok": true, ..., "default_ready": true}
```

对外访问：在云厂商安全组放行 8787 端口（或自行加 Nginx/Caddy 反代 + 域名 + HTTPS）。

## 日常更新

```bash
git pull
docker compose up -d --build   # 依赖不变时只需数秒
```

## 换 Key / 换模型

```bash
vim webapp/.env
docker compose restart         # 不需要重新构建镜像
```

## 常用命令

```bash
docker compose logs -f         # 看日志
docker compose restart         # 重启
docker compose down            # 停止并移除容器
docker compose up -d --build   # 代码更新后重建
```

## 安全清单

- [x] `webapp/.env` 在 `.gitignore` 和 `.dockerignore` 里，git 与镜像都拿不到它
- [x] 镜像内以非 root 用户（appuser）运行
- [ ] 服务器防火墙/安全组只放行需要的端口
- [ ] 你的云厂商有自己的认证/防护，按其规范配置即可

## 提醒

- 服务无认证、无频控：知道地址的人都会消耗 `.env` 里的额度，请把地址仅
  分享给目标用户（朋友），不要公开发到群里/网上。
- 如后续想加访问口令或频控，在后端加一个中间件即可，随时可以做。
