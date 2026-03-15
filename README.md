# auto-crawler

山西电力交易平台自动化爬虫服务，将数据采集、校验、转换、上传的完整流程拆分为两个独立微服务，通过 Docker Compose 部署。

---

## 项目结构

```
auto-crawler/
├── crawler/                        # 爬虫脚本（已有，不修改）
├── data-verify/                    # 数据校验脚本（已有，不修改）
├── post-process/                   # 数据转换脚本（已有，不修改）
├── crawler-service/                # 采集服务（服务器 A）
│   ├── base/
│   │   ├── Dockerfile
│   │   └── requirements-base.txt
│   ├── main.py
│   ├── pipeline.py
│   ├── crawler_runner.py
│   ├── verify_runner.py
│   ├── uploader.py
│   ├── notifier.py
│   ├── config_loader.py
│   ├── logger.py
│   ├── config.yaml
│   ├── requirements.txt
│   └── Dockerfile
├── processor-service/              # 处理服务（服务器 B）
│   ├── base/
│   │   ├── Dockerfile
│   │   └── requirements-base.txt
│   ├── main.py
│   ├── api.py
│   ├── pipeline.py
│   ├── processor_runner.py
│   ├── uploader.py
│   ├── platform_notifier.py
│   ├── config_loader.py
│   ├── logger.py
│   ├── config.yaml
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── init.sh
```

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│  服务器 A：采集服务（crawler-service）                            │
│                                                                  │
│  自动爬取 → 自动校验 → [校验失败则重新爬取] → 自动打包上传         │
└─────────────────────────────────────────────────────────────────┘
                              │ 上传完成后 HTTP 回调通知
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  服务器 B：处理服务（processor-service）                          │
│                                                                  │
│  自动数据转换 → 自动打包上传 → 自动通知三方平台                     │
└─────────────────────────────────────────────────────────────────┘
```

两个服务均通过 MinIO 对象存储传递数据：服务器 A 将原始爬取数据打包上传至 `sxpx` bucket，通知服务器 B 后，服务器 B 下载、转换，再将宽表结果上传至 `sxpx` bucket。

---

## 已有脚本说明

三个已有脚本目录由两个微服务通过 `subprocess` 调用，不做任何修改。

| 目录 | 入口 | 功能 |
|------|------|------|
| `crawler/` | `main.py` | 通过 Playwright CDP 连接已登录的 Chrome，爬取山西电力交易平台 19 类现货市场数据，输出 CSV/Excel 到 `crawler/data/` |
| `data-verify/` | `analyze_excel.py` | 对 `crawler/data/` 下的文件执行日期一致性、通道一致性、文件完整性校验，输出 `loss.txt` 和 `validation_errors.txt` |
| `post-process/` | `batch_process_data.py` | 将 CSV/Excel 转换为规范宽表，统一时间格式，处理枚举映射，单文件超 5MB 自动拆分，输出到 `post-process/output/` |

---

## 采集服务（crawler-service）

### 流程

```
Step 1  爬取
Step 2  校验（读取 loss.txt 判断是否通过）
Step 3  校验失败 → 批量重爬（最多 3 轮，每轮间隔 30s）
Step 4  整理文件 → 打包 → 上传 MinIO → 通知服务器 B
```

### 触发模式

**定时日增模式（默认）**

```bash
python main.py --mode scheduled
```

服务启动后进入阻塞调度循环，按 `config.yaml` 中 `schedule.cron` 配置的 cron 表达式定时触发（默认每天 08:30），每次爬取 `date_offset_days`（默认 `-1`，即昨天）对应的数据。

**手动批量模式**

```bash
python main.py --mode batch --start 2025-01-01 --end 2025-03-31
python main.py --mode batch --start 2025-01-01 --end 2025-01-31 --task 日前备用总量,断面约束
```

指定日期范围和任务范围立即执行一次后退出，适用于历史数据补录。

### 校验与重爬逻辑

校验完成后读取 `data-verify/loss.txt`，跳过注释行和空行，若无有效数据行则判定为 PASS，否则判定为 FAIL 并进入重爬。重爬通过 `--loss-file` 参数将 `loss.txt` 传给爬虫，批量补充下载缺失数据，最多重爬 3 轮。超出最大轮次后记录告警，继续执行打包上传，不因部分数据缺失阻塞整个流程。

### 配置文件（`crawler-service/config.yaml`）

```yaml
schedule:
  cron: "30 8 * * *"       # 触发时间（标准 cron 表达式）
  timezone: "Asia/Shanghai"
  date_offset_days: -1     # 目标日期偏移（-1=昨天）

crawler:
  script_path: "../crawler/main.py"
  config_path: "../crawler/config.yaml"
  data_dir: "../crawler/data"
  cleanup_after_upload: true  # 上传后是否清除 crawler/data/

verify:
  script_path: "../data-verify/analyze_excel.py"
  loss_file: "../data-verify/loss.txt"
  max_retry_rounds: 3
  retry_interval_seconds: 30

upload:
  type: "minio"
  endpoint: "http://crawler-minio-service:9000"
  access_key: "${CRAWLER_MINIO_ACCESS_KEY}"
  secret_key: "${CRAWLER_MINIO_SECRET_KEY}"
  bucket: "sxpx"
  prefix: "raw/"

notify:
  enabled: true
  url: "http://crawler-processor-service:8301/api/trigger"
  secret: "${CRAWLER_NOTIFY_SECRET}"
  retry_times: 3
  retry_interval_seconds: 30
```

---

## 处理服务（processor-service）

### 流程

```
Step 1  接收 HTTP 触发信号（POST /api/trigger），立即返回 202，后台异步处理
Step 2  从 MinIO 下载 tar 包 → MD5 校验 → 解压 → 数据转换
Step 3  打包转换结果 → 上传 MinIO（sxpx bucket）
Step 4  通知三方平台
```

### 幂等处理

以 `object_name`（tar 包文件名，如 `2025-01-13.tar.gz`）作为幂等键，状态持久化到 `processed_jobs.json`。`processing` 和 `done` 状态返回 `409 Conflict`，`failed` 状态允许重新触发。服务启动时自动将超时（默认 600s）的 `processing` 记录重置为 `failed`，防止异常重启后任务永久卡死。

### HTTP 接口

```
POST /api/trigger
Content-Type: application/json

{
  "object_name": "2025-01-13.tar.gz",
  "download_url": "http://crawler-minio-service:9000/sxpx/raw/2025-01-13.tar.gz",
  "md5": "abc123...",
  "date_range": {"start": "2025-01-13", "end": "2025-01-13"},
  "categories": ["机组实际发电曲线", "日前联络线计划"],
  "timestamp": "2025-01-14T08:31:00"
}
```

响应：`202 Accepted`（新任务）/ `409 Conflict`（已处理或处理中）

### 配置文件（`processor-service/config.yaml`）

```yaml
server:
  host: "0.0.0.0"
  port: 8301
  secret: "${CRAWLER_PROCESSOR_SERVER_SECRET}"
  jobs_file: "./processed_jobs.json"
  processing_timeout: 600

storage:
  type: "minio"
  endpoint: "http://crawler-minio-service:9000"
  access_key: "${CRAWLER_MINIO_ACCESS_KEY}"
  secret_key: "${CRAWLER_MINIO_SECRET_KEY}"
  bucket: "sxpx"
  prefix: "raw/"
  local_cache_dir: "./cache"

processor:
  script_path: "../post-process/batch_process_data.py"
  data_dir: "../post-process/data"
  output_dir: "../post-process/output"
  cleanup_after_upload: true

upload:
  type: "minio"
  bucket: "sxpx"
  prefix: "output/"

notify:
  platforms:
    - name: "platform-a"
      enabled: true
      url: "https://api.platform-a.com/data/notify"
      token: "platform-a-token"
      retry_times: 5
      retry_interval_seconds: 30
```

---

## 部署

### 前置条件

- Docker 20.10+，Docker Compose v2
- 宿主机已安装并登录 Chrome（供爬虫 CDP 连接，仅服务器 A 需要）
- MinIO 服务可访问

### 首次部署

**1. 克隆仓库并配置环境变量**

```bash
cp .env.example .env
# 编辑 .env，填入真实的 MinIO 密钥和服务密钥
```

`.env` 文件内容：

```dotenv
CRAWLER_MINIO_ACCESS_KEY=your-access-key
CRAWLER_MINIO_SECRET_KEY=your-secret-key
CRAWLER_NOTIFY_SECRET=your-notify-secret
CRAWLER_PROCESSOR_SERVER_SECRET=your-server-secret
```

**2. 初始化宿主机目录和文件**

```bash
bash init.sh
```

该脚本会创建以下目录和文件：

```
data/minio/
data/crawler/
data/data-verify/loss.txt
data/data-verify/validation_errors.txt
data/post-process/data/
data/post-process/output/
data/crawler-processor-service/cache/
data/crawler-processor-service/processed_jobs.json   # 内容为 {}
logs/crawler-service/
logs/crawler-processor-service/
```

> `processed_jobs.json` 必须包含合法 JSON（`{}`），不可用 `touch` 创建空文件，否则服务启动时解析失败。`init.sh` 已处理此细节。

**3. 构建镜像**

```bash
# 先构建基础镜像（含系统依赖，变更不频繁）
docker compose --profile build-only build

# 再构建服务镜像（叠加业务代码）
docker compose build
```

**4. 启动服务**

```bash
docker compose up -d
```

### 日常操作

```bash
# 查看日志
docker compose logs -f crawler-service
docker compose logs -f crawler-processor-service

# 仅更新业务代码后重建并重启
docker compose up -d --build crawler-service
docker compose up -d --build crawler-processor-service

# 依赖变更后重建基础镜像
docker compose --profile build-only build crawler-service-base
docker compose build crawler-service

# 停止服务
docker compose down
```

### 手动批量补录

```bash
docker compose exec crawler-service python main.py \
  --mode batch \
  --start 2025-01-01 \
  --end 2025-01-31
```

---

## 直接部署（不使用容器）

适用于无法使用 Docker 的目标服务器，直接在宿主机上运行两个服务。

### 前置条件

- Python 3.11+
- Chrome 浏览器已安装并以 CDP 模式运行（仅服务器 A 需要）
- MinIO 服务可访问
- 两台服务器均已克隆本仓库到相同路径（或按实际路径修改 `config.yaml`）

### 服务器 A：采集服务（crawler-service）

**1. 创建并激活虚拟环境**

```bash
cd auto-crawler/crawler-service
python3 -m venv venv
source venv/bin/activate
```

**2. 安装依赖**

```bash
# 安装 crawler-service 自身依赖
pip install -r requirements.txt

# 安装爬虫脚本依赖
pip install -r ../crawler/requirements.txt

# 安装校验脚本依赖
pip install -r ../data-verify/requirements.txt

# 安装 Playwright 浏览器驱动
playwright install chromium
```

> 若服务器无法访问外网，可在有网络的机器上执行 `pip download` 打包后离线安装。

**3. 安装系统依赖（Chromium 运行时库）**

```bash
# Debian / Ubuntu
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
  libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
  fonts-unifont fonts-noto-color-emoji

# CentOS / RHEL（包名略有差异）
sudo yum install -y nss nspr atk at-spi2-atk cups-libs libdrm dbus-libs \
  libxkbcommon libXcomposite libXdamage libXfixes libXrandr mesa-libgbm \
  alsa-lib pango cairo
```

**4. 配置环境变量**

```bash
export CRAWLER_MINIO_ACCESS_KEY=your-access-key
export CRAWLER_MINIO_SECRET_KEY=your-secret-key
export CRAWLER_NOTIFY_SECRET=your-notify-secret
# 可选，默认 INFO
export LOG_LEVEL=INFO
```

建议写入 `~/.bashrc` 或 `~/.zshrc` 使其持久化，或创建 `.env` 文件后在启动脚本中 `source` 它。

**5. 修改 `config.yaml`**

将服务间地址从容器名改为实际 IP/域名：

```yaml
upload:
  endpoint: "http://<minio服务器IP>:29000"   # MinIO 实际地址

notify:
  url: "http://<服务器B的IP>:28301/api/trigger"  # 服务器 B 实际地址
```

**6. 创建必要目录**

```bash
mkdir -p logs
mkdir -p ../crawler/data
mkdir -p ../data-verify
touch ../data-verify/loss.txt
touch ../data-verify/validation_errors.txt
```

**7. 启动服务**

定时模式（后台运行）：

```bash
nohup python main.py --mode scheduled > logs/stdout.log 2>&1 &
echo $! > crawler-service.pid
```

手动批量模式：

```bash
python main.py --mode batch --start 2025-01-01 --end 2025-01-31
python main.py --mode batch --start 2025-01-01 --end 2025-01-31 --task 日前备用总量,断面约束
```

**8. 停止服务**

```bash
kill $(cat crawler-service.pid)
```

---

### 服务器 B：处理服务（processor-service）

**1. 创建并激活虚拟环境**

```bash
cd auto-crawler/processor-service
python3 -m venv venv
source venv/bin/activate
```

**2. 安装依赖**

```bash
pip install -r requirements.txt
pip install -r ../post-process/requirements.txt
```

**3. 配置环境变量**

```bash
export CRAWLER_MINIO_ACCESS_KEY=your-access-key
export CRAWLER_MINIO_SECRET_KEY=your-secret-key
export CRAWLER_PROCESSOR_SERVER_SECRET=your-server-secret
export LOG_LEVEL=INFO
```

**4. 修改 `config.yaml`**

```yaml
server:
  host: "0.0.0.0"
  port: 8301

storage:
  endpoint: "http://<minio服务器IP>:29000"   # MinIO 实际地址
```

**5. 创建必要目录和文件**

```bash
mkdir -p logs cache
mkdir -p ../post-process/data ../post-process/output
echo '{}' > processed_jobs.json   # 必须是合法 JSON，不可用 touch 创建空文件
```

**6. 启动服务**

```bash
nohup python main.py > logs/stdout.log 2>&1 &
echo $! > processor-service.pid
```

**7. 停止服务**

```bash
kill $(cat processor-service.pid)
```

---

### 使用 systemd 管理进程（推荐）

相比 `nohup`，systemd 可在服务崩溃或服务器重启后自动拉起进程。

以采集服务为例，创建 `/etc/systemd/system/crawler-service.service`：

```ini
[Unit]
Description=Auto Crawler Service
After=network.target

[Service]
Type=simple
User=<运行用户>
WorkingDirectory=/path/to/auto-crawler/crawler-service
Environment=CRAWLER_MINIO_ACCESS_KEY=your-access-key
Environment=CRAWLER_MINIO_SECRET_KEY=your-secret-key
Environment=CRAWLER_NOTIFY_SECRET=your-notify-secret
ExecStart=/path/to/auto-crawler/crawler-service/venv/bin/python main.py --mode scheduled
Restart=on-failure
RestartSec=10
StandardOutput=append:/path/to/auto-crawler/crawler-service/logs/stdout.log
StandardError=append:/path/to/auto-crawler/crawler-service/logs/stderr.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable crawler-service
sudo systemctl start crawler-service
sudo systemctl status crawler-service
```

处理服务（processor-service）按同样方式创建对应的 `.service` 文件，`ExecStart` 改为 `python main.py` 即可。

---

### 查看日志

```bash
# 实时跟踪滚动日志文件
tail -f auto-crawler/crawler-service/logs/crawler-service.log

# 若使用 systemd
journalctl -u crawler-service -f
```

---

## 日志

两个服务的日志均输出到控制台和滚动文件（单文件最大 10MB，保留 5 个备份），持久化到宿主机：

- `logs/crawler-service/` → 容器内 `/app/crawler-service/logs/`
- `logs/crawler-processor-service/` → 容器内 `/app/crawler-processor-service/logs/`

日志格式：`%(asctime)s [%(levelname)s] %(name)s: %(message)s`

默认级别为 `INFO`，可通过环境变量覆盖：

```yaml
# docker-compose.yml 中添加
environment:
  - LOG_LEVEL=DEBUG
```

每个步骤的开始与完成均以 `[Step N]` 前缀记录，便于快速定位流程阶段。上传完成后在日志目录生成 `upload_manifest_YYYYMMDD_HHmmss.json` 清单文件，记录 tar 包对象名、文件大小、MD5 和上传时间。

---

## 网络说明

所有服务均接入 `crawler-net` bridge 网络，服务间通过容器名互相访问。

| 服务 | 宿主机端口 | 容器端口 |
|------|-----------|---------|
| `crawler-minio-service` | 29000 / 29001 | 9000 / 9001 |
| `crawler-service` | 28300 | 8300 |
| `crawler-processor-service` | 28301 | 8301 |

`crawler-service` 需要访问宿主机 Chrome 的 CDP 端口（默认 9222），请确保宿主机防火墙允许容器访问该端口，或在 `docker-compose.yml` 中为 `crawler-service` 添加 `extra_hosts: ["host-gateway:host-gateway"]`。

---

## 敏感配置

所有密钥通过环境变量注入，配置文件中使用 `${VAR_NAME}` 占位符，由 `config_loader.py` 在运行时替换。`.env` 文件不提交到版本库，仅提交 `.env.example` 作为模板。

| 环境变量 | 用途 |
|----------|------|
| `CRAWLER_MINIO_ACCESS_KEY` | MinIO Access Key（两个服务共用） |
| `CRAWLER_MINIO_SECRET_KEY` | MinIO Secret Key（两个服务共用） |
| `CRAWLER_NOTIFY_SECRET` | 服务器 A 通知服务器 B 时携带的 `X-Secret` 请求头 |
| `CRAWLER_PROCESSOR_SERVER_SECRET` | 服务器 B HTTP 接口鉴权密钥（预留，当前不校验） |
