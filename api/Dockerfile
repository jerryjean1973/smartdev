FROM python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client bind9-dnsutils && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM runtime AS source
COPY *.py *.sql github_known_hosts smd_workbench.tar.gz ./
COPY tenant-image-excludes.txt /tmp/tenant-image-excludes.txt
ARG SMARTDEV_IMAGE_PROFILE=command-center
RUN case "$SMARTDEV_IMAGE_PROFILE" in command-center) ;; tenant-smd) \
      while IFS= read -r path; do case "$path" in ''|'#'*) continue;; esac; rm -f -- "/app/$path"; done < /tmp/tenant-image-excludes.txt ;; \
      *) echo "Unsupported SmartDev image profile" >&2; exit 2;; esac

FROM runtime
ARG SMARTDEV_IMAGE_PROFILE=command-center
ENV SMARTDEV_IMAGE_PROFILE=$SMARTDEV_IMAGE_PROFILE
LABEL org.smartdev.image-profile=$SMARTDEV_IMAGE_PROFILE
COPY --from=source /app /app
RUN printf 'smartdev:x:65532:65532:SmartDev API:/nonexistent:/usr/sbin/nologin\n' >> /etc/passwd
USER 65532:65532
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
