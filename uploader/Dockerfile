FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV AUTH_ENABLED=false
ENV UPLOADER_USERNAME=admin
ENV UPLOADER_PASSWORD=changeme
ENV UPLOADER_BIND=0.0.0.0
ENV UPLOADER_PORT=8080
ENV UPLOADER_TARGET=/data/export.zip

EXPOSE 8080

CMD ["python", "app.py"]
