ARG NODE_BUILD_IMAGE=node:22-alpine
ARG NGINX_RUNTIME_IMAGE=nginx:1.27-alpine

FROM ${NODE_BUILD_IMAGE} AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json env.d.ts vite.config.ts ./
COPY src ./src
RUN npm run build

FROM ${NGINX_RUNTIME_IMAGE} AS runtime

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=20 \
  CMD wget -qO- http://127.0.0.1/health >/dev/null || exit 1
