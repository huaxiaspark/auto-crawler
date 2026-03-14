FROM processor-service-base:latest

WORKDIR /app/processor-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py api.py processor_runner.py uploader.py platform_notifier.py config_loader.py logger.py ./

RUN mkdir -p /app/processor-service/logs && chmod 777 /app/processor-service/logs

CMD ["python", "main.py"]
