FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY public ./public
COPY app ./app
ARG SMARTDEV_IMAGE_PROFILE=command-center
RUN case "$SMARTDEV_IMAGE_PROFILE" in command-center) rm -f app/*.tenant.tsx ;; tenant-smd) \
      cp app/admin-projects.tenant.tsx app/admin-projects.tsx && \
      cp app/smd-deployment.tenant.tsx app/smd-deployment.tsx && \
      cp app/docker-download.tenant.tsx app/docker-download.tsx && \
      rm -f app/*.tenant.tsx app/compute-console.tsx app/deployment-activity.tsx app/deployment-configuration.tsx \
        app/deployment-login.tsx app/smd-connections.tsx app/deployment-console.module.css ;; \
      *) echo "Unsupported SmartDev image profile" >&2; exit 2;; esac
RUN npm run build
FROM node:22-bookworm-slim
WORKDIR /app
ARG SMARTDEV_IMAGE_PROFILE=command-center
ENV SMARTDEV_IMAGE_PROFILE=$SMARTDEV_IMAGE_PROFILE
LABEL org.smartdev.image-profile=$SMARTDEV_IMAGE_PROFILE
COPY --from=build --chown=node:node /app/package.json /app/package.json
COPY --from=build --chown=node:node /app/node_modules /app/node_modules
COPY --from=build --chown=node:node /app/public /app/public
COPY --from=build --chown=node:node /app/.next /app/.next
USER node
EXPOSE 3000
CMD ["npm", "start"]
