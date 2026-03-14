FROM crawler-processor-service-base:latest

WORKDIR /app/crawler-processor-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py api.py processor_runner.py uploader.py platform_notifier.py config_loader.py logger.py pipeline.py ./

RUN mkdir -p /app/crawler-processor-service/logs && chmod 777 /app/crawler-processor-service/logs

CMD ["python", "main.py"]
