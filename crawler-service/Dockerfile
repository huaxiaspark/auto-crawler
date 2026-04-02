FROM crawler-service-base:latest

WORKDIR /app/crawler-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py pipeline.py crawler_runner.py verify_runner.py uploader.py notifier.py config_loader.py logger.py keepalive.py runtime_guard.py ./

RUN mkdir -p /app/crawler-service/logs && chmod 777 /app/crawler-service/logs

CMD ["python", "main.py"]
