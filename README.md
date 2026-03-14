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

两个服务均通过 MinIO 对象存储传递数据：服务器 A 将原始爬取数据打包上传至 `sxpx-raw` bucket，通知服务器 B 后，服务器 B 下载、转换，再将宽表结果上传至 `sxpx-output` bucket。

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
  cleanup_after_upload: false  # 上传后是否清除 crawler/data/

verify:
  script_path: "../data-verify/analyze_excel.py"
  loss_file: "../data-verify/loss.txt"
  max_retry_rounds: 3
  retry_interval_seconds: 30

upload:
  type: "minio"
  endpoint: "http://minio-server:9000"
  access_key: "${CRAWLER_MINIO_ACCESS_KEY}"
  secret_key: "${CRAWLER_MINIO_SECRET_KEY}"
  bucket: "sxpx-raw"
  prefix: "data/"

notify:
  enabled: true
  url: "http://processor-service:8080/api/trigger"
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
Step 3  打包转换结果 → 上传 MinIO（sxpx-output bucket）
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
  "download_url": "http://minio-server:9000/sxpx-raw/data/2025-01-13.tar.gz",
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
  port: 8080
  secret: "${CRAWLER_PROCESSOR_SERVER_SECRET}"
  jobs_file: "./processed_jobs.json"
  processing_timeout: 600

storage:
  type: "minio"
  endpoint: "http://minio-server:9000"
  access_key: "${CRAWLER_MINIO_ACCESS_KEY}"
  secret_key: "${CRAWLER_MINIO_SECRET_KEY}"
  bucket: "sxpx-raw"
  prefix: "data/"
  local_cache_dir: "./cache"

processor:
  script_path: "../post-process/batch_process_data.py"
  data_dir: "../post-process/data"
  output_dir: "../post-process/output"
  cleanup_after_upload: true

upload:
  type: "minio"
  bucket: "sxpx-output"
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
data/crawler/
data/data-verify/loss.txt
data/data-verify/validation_errors.txt
data/post-process/data/
data/post-process/output/
data/processor-service/cache/
data/processor-service/processed_jobs.json   # 内容为 {}
logs/crawler-service/
logs/processor-service/
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
docker compose logs -f processor-service

# 仅更新业务代码后重建并重启
docker compose up -d --build crawler-service
docker compose up -d --build processor-service

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

## 日志

两个服务的日志均输出到控制台和滚动文件（单文件最大 10MB，保留 5 个备份），持久化到宿主机：

- `logs/crawler-service/` → 容器内 `/app/crawler-service/logs/`
- `logs/processor-service/` → 容器内 `/app/processor-service/logs/`

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

`crawler-service` 使用 `network_mode: host`，以便容器内进程访问宿主机 Chrome 的 CDP 端口（默认 9222）。因此该服务不使用 `ports` 映射，服务器 A 上的其他服务可直接通过 `localhost:8080` 访问 `processor-service`（若两个服务部署在同一台机器上）。

`processor-service` 暴露 `8080` 端口，接收服务器 A 的 HTTP 回调通知。

---

## 敏感配置

所有密钥通过环境变量注入，配置文件中使用 `${VAR_NAME}` 占位符，由 `config_loader.py` 在运行时替换。`.env` 文件不提交到版本库，仅提交 `.env.example` 作为模板。

| 环境变量 | 用途 |
|----------|------|
| `CRAWLER_MINIO_ACCESS_KEY` | MinIO Access Key（两个服务共用） |
| `CRAWLER_MINIO_SECRET_KEY` | MinIO Secret Key（两个服务共用） |
| `CRAWLER_NOTIFY_SECRET` | 服务器 A 通知服务器 B 时携带的 `X-Secret` 请求头 |
| `CRAWLER_PROCESSOR_SERVER_SECRET` | 服务器 B HTTP 接口鉴权密钥（预留，当前不校验） |
