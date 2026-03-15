# 数据处理流程接入说明

本文档说明如何通过命令行手动触发完整数据处理流程，以及各环节的验证方法。适用于真实第三方系统接入和本地联调验证。

---

## 服务端口一览

| 服务 | 容器名 | 本地端口 |
|------|--------|----------|
| crawler-service | auto-crawler-crawler | 28300 |
| processor-service | auto-crawler-processor | 28301 |
| mock-notify-receiver | auto-crawler-mock-notify-receiver | 28401 |
| MinIO | 远程部署（112.126.80.142） | 29000 |

---

## 完整流程说明

```
crawler/data/（已爬取数据，含子目录 exports/ 和 现货出清结果/）
    ↓ flatten_and_classify（整理文件结构）
    ↓ 打包上传
MinIO: sxpx/raw/{date}.tar.gz
    ↓ POST /api/trigger（X-Secret 鉴权）
processor-service（下载 → MD5 校验 → 数据转换 → 打包上传）
    ↓
MinIO: sxpx/output/{date}.tar.gz
    ↓ POST /data/notify  +  POST /webhook
mock-notify-receiver（或真实三方平台）
```

---

## Step 0：数据校验（可选，建议在上传前执行）

爬取完成后，先对 `crawler/data/` 中的数据执行校验，确认文件完整性和内容一致性：

```bash
cd data-verify
source venv/bin/activate
python3 analyze_excel.py --start 2025-01-01 --end 2025-01-01
```

校验结果：
- 无缺失文件：`loss.txt` 为空或不存在，可直接进入上传步骤
- 有缺失文件：`loss.txt` 中列出缺失条目，需补爬后重新校验

校验报告输出至：
- `data-verify/loss.txt` — 缺失文件列表（仅在有缺失时写入）
- `data-verify/validation_errors.txt` — 内容一致性错误详情

---

## Step 1：打包上传原始数据到 MinIO

此步骤由 `crawler-service` 自动完成，也可手动执行（在 `crawler-service/` 目录下）：

```bash
cd crawler-service
source venv/bin/activate
export $(cat ../.env | xargs)

python3 -c "
import os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
sys.path.insert(0, '.')
import config_loader, uploader, pipeline
from datetime import datetime

config = config_loader.load_config('config.yaml')
data_dir = os.path.abspath(config['crawler']['data_dir'])
object_name = '2025-01-01.tar.gz'

pipeline._flatten_and_classify(data_dir)
md5, size_bytes = uploader.pack_and_upload(
    source_dir=data_dir,
    object_name=object_name,
    config=config['upload'],
)
uploader.write_manifest(
    log_dir=config['log_dir'],
    object_name=object_name,
    size_bytes=size_bytes,
    md5=md5,
    uploaded_at=datetime.now().isoformat(),
)
print(f'md5={md5} size={size_bytes}')
"
```

**说明：**
- `_flatten_and_classify` 会先将 `exports/`、`现货出清结果/` 等子目录中的文件展平到根目录，再按任务名重新归类
- 上传目标：`sxpx/raw/{date}.tar.gz`
- 上传清单写入：`crawler-service/logs/upload_manifest_{timestamp}.json`

记录输出的 `md5` 值，后续步骤需要用到。

---

## Step 2：触发 processor-service 处理

向 `processor-service` 发送触发信号，启动下载 → 数据转换 → 上传流程。

```bash
curl -X POST http://localhost:28301/api/trigger \
  -H "Content-Type: application/json" \
  -H "X-Secret: secret" \
  -d '{
    "object_name": "2025-01-01.tar.gz",
    "download_url": "http://112.126.80.142:29000/sxpx/raw/2025-01-01.tar.gz",
    "md5": "<Step1输出的md5>",
    "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
    "categories": ["实时市场出清概况", "日前市场出清概况", "抽蓄电站水位", "断面约束", "机组实际发电曲线"],
    "timestamp": "2025-01-01T08:00:00.000000"
  }'
```

**成功响应：**
```json
{"detail": "accepted", "object_name": "2025-01-01.tar.gz"}
```

**幂等响应（已处理过）：**
```json
{"detail": "Job 2025-01-01.tar.gz already done"}
```
HTTP 状态码 409，表示该任务已处理，无需重复触发。

**请求字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| object_name | string | MinIO 中的文件名，格式 `{date}.tar.gz` |
| download_url | string | 完整下载地址（processor 内部忽略此字段，直接从 MinIO 下载） |
| md5 | string | 原始包的 MD5，用于完整性校验 |
| date_range.start | string | 数据起始日期，格式 `YYYY-MM-DD` |
| date_range.end | string | 数据结束日期，格式 `YYYY-MM-DD` |
| categories | array | 任务名列表，透传给三方通知，如 `["抽蓄电站水位", "断面约束", "机组实际发电曲线"]` |
| timestamp | string | 触发时间戳（ISO 8601） |

---

## Step 3：查询处理状态

processor-service 处理为异步，触发后可通过日志确认完成：

```bash
docker logs auto-crawler-processor --tail 50 -f
```

处理完成后日志末尾会出现：
```
[INFO] pipeline: processor pipeline 完成，耗时 Xs
[INFO] api: 任务完成，object_name=2025-01-01.tar.gz
```

---

## Step 4：验证三方平台收到通知

### 查看 mock-notify-receiver 接收记录

```bash
curl http://localhost:28401/records
```

**成功响应示例：**
```json
{
  "total": 2,
  "records": [
    {
      "received_at": "2026-03-15T02:58:05.879554",
      "endpoint": "/data/notify",
      "categories": ["实时市场出清概况", "日前市场出清概况", "抽蓄电站水位", "断面约束", "机组实际发电曲线"],
      "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
      "object_name": "2025-01-01.tar.gz",
      "md5": "176b87546574fc271a75bb4ea87ae755",
      "download_url": "http://112.126.80.142:29000/sxpx/output/2025-01-01.tar.gz"
    },
    {
      "received_at": "2026-03-15T02:58:05.889549",
      "endpoint": "/webhook",
      "categories": ["实时市场出清概况", "日前市场出清概况", "抽蓄电站水位", "断面约束", "机组实际发电曲线"],
      "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
      "object_name": "2025-01-01.tar.gz",
      "md5": "176b87546574fc271a75bb4ea87ae755",
      "download_url": "http://112.126.80.142:29000/sxpx/output/2025-01-01.tar.gz"
    }
  ]
}
```

processor 会同时通知两个端点：`/data/notify` 和 `/webhook`，因此 `total` 应为 2。

### 清空记录（重新测试前）

```bash
curl -X DELETE http://localhost:28401/records
```

---

## 真实三方平台接入

真实平台需实现以下接口之一（或两者），接收 processor 的 POST 通知：

### 接口规范

**请求方式：** `POST`

**请求头：**
```
Content-Type: application/json
Authorization: Bearer <token>   # 若配置了 token
```

**请求体：**
```json
{
  "categories": ["实时市场出清概况", "日前市场出清概况", "抽蓄电站水位", "断面约束", "机组实际发电曲线"],
  "date_range": {"start": "2025-01-01", "end": "2025-01-01"},
  "object_name": "2025-01-01.tar.gz",
  "md5": "176b87546574fc271a75bb4ea87ae755",
  "download_url": "http://112.126.80.142:29000/sxpx/output/2025-01-01.tar.gz"
}
```

**成功响应：** HTTP 2xx，响应体不限。

### 配置接入

在 `processor-service/config.yaml` 的 `notify.platforms` 中添加平台配置：

```yaml
notify:
  platforms:
    - name: "your-platform"
      enabled: true
      url: "https://your-platform.com/data/notify"
      token: "your-token"
      retry_times: 5
      retry_interval_seconds: 30
```

重启 processor-service 后生效：

```bash
docker compose restart crawler-processor-service
```

---

## 常见问题

**Q: 触发后返回 409，如何重新处理？**

重置 jobs 文件后重启服务：
```bash
echo '{}' > data/crawler-processor-service/processed_jobs.json
docker compose restart crawler-processor-service
```

**Q: 如何确认文件已上传到 MinIO？**

MinIO 部署在远程服务器，通过 processor 日志确认上传结果：
```bash
docker logs auto-crawler-processor --tail 50
```

日志中会出现：
```
[INFO] uploader: [Step 3] 上传完成，object=output/2025-01-01.tar.gz，md5=...，size=... bytes
```

**Q: processor 处理失败如何排查？**

```bash
docker logs auto-crawler-processor --tail 100
```

查看 `[ERROR]` 行定位问题。

**Q: 数据校验发现缺失文件怎么办？**

查看 `data-verify/loss.txt` 中的缺失条目，使用爬虫补爬后重新执行校验：
```bash
cd data-verify
source venv/bin/activate
python3 analyze_excel.py --start 2025-01-01 --end 2025-01-01
```

确认 `loss.txt` 为空后再执行上传步骤。
