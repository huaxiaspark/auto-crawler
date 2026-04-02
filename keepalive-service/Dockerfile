FROM crawler-service-base:latest

WORKDIR /app/keepalive-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py keepalive.py runtime_guard.py config_loader.py logger.py ./

RUN mkdir -p /app/keepalive-service/logs && chmod 777 /app/keepalive-service/logs

CMD ["python", "main.py"]
