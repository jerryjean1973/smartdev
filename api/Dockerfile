FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client bind9-dnsutils && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.sql github_known_hosts ./
RUN printf 'smartdev:x:65532:65532:SmartDev API:/nonexistent:/usr/sbin/nologin\n' >> /etc/passwd
USER 65532:65532
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
