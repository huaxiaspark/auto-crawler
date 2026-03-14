#!/usr/bin/env bash
# init.sh —— 首次部署前执行，创建所有必要的宿主机目录和文件
set -e

mkdir -p data/minio
mkdir -p data/crawler
mkdir -p data/data-verify
mkdir -p data/post-process/data
mkdir -p data/post-process/output
mkdir -p data/crawler-processor-service/cache
mkdir -p logs/crawler-service
mkdir -p logs/crawler-processor-service

chmod 777 logs/crawler-service logs/crawler-processor-service

touch data/data-verify/loss.txt
touch data/data-verify/validation_errors.txt
echo '{}' > data/crawler-processor-service/processed_jobs.json

echo "初始化完成"
