# 06. Docker 部署规范

## 1. 部署目标

项目必须满足：

- 新环境只需Docker与Docker Compose。
- 复制 `.env.example` 后一条命令启动完整系统。
- 基础设施、应用和初始化任务均容器化。
- 所有镜像使用固定版本或内容摘要，不使用 `latest`。
- 默认只向宿主机暴露前端入口。
- 开发环境可以通过覆盖文件暴露调试端口。
- 数据存储使用命名卷，重启容器不丢失。

## 2. Compose文件布局

```text
compose.yaml                    # 基础完整部署，仅暴露前端
compose.dev.yaml                # 开发覆盖：暴露Java/Python/DB等端口
.env.example                    # 无密钥模板
.env                            # 本地密钥，不提交
deploy/
├─ mysql/
│  └─ init/00-create-databases.sh
├─ rocketmq/
│  └─ broker.conf
├─ nginx/
│  └─ default.conf
└─ rag-documents/
   ├─ meeting-room-policy.md
   ├─ vip-room-policy.md
   └─ architecture-review-standard.md
```

## 3. 服务清单

| Compose服务 | 容器端口 | 宿主开发端口 | 说明 |
|---|---:|---:|---|
| frontend | 80 | 80 | Nginx静态前端与反向代理 |
| business-service | 8080 | 8080 | Spring Boot |
| agent-service | 8000 | 8000 | FastAPI/LangGraph |
| video-provider-mock | 8010 | 8010 | 视频会议外部服务Mock |
| mysql | 3306 | 3306 | 两个逻辑数据库 |
| redis | 6379 | 6379 | 预占、限流、checkpoint |
| rocketmq-namesrv | 9876 | 9876 | RocketMQ NameServer |
| rocketmq-broker | 10911 | 10911 | RocketMQ Broker |
| rocketmq-topic-init | 无 | 无 | 一次性创建 Day 3 Topic 与 Consumer Group |
| qdrant | 6333 | 6333 | 向量数据库HTTP |
| rag-init | 无 | 无 | 一次性RAG入库任务 |

基础 `compose.yaml` 只发布 `frontend:80`。其他端口通过 `expose` 在内部网络可见；`compose.dev.yaml` 再映射到宿主机。

## 4. 网络设计

```text
edge_net:
  frontend

backend_net (internal):
  frontend
  business-service
  agent-service
  video-provider-mock
  mysql
  redis
  rocketmq-namesrv
  rocketmq-broker
  qdrant

agent_egress_net:
  agent-service
```

- 浏览器只能访问frontend。
- `/api/*`由Nginx转发到business-service。
- business-service通过Compose DNS名称访问agent-service。
- agent-service通过Compose DNS名称访问business-service内部Tool API。
- MySQL、Redis、RocketMQ和Qdrant不加入edge_net。
- backend_net禁止直接访问外网；agent-service额外加入agent_egress_net以调用DeepSeek和首次下载Embedding模型。
- 其他业务和基础设施容器不加入agent_egress_net。

## 5. 命名卷

```text
mysql_data
redis_data
rocketmq_broker_store
qdrant_data
agent_model_cache
```

用途：

- `mysql_data`：业务、Outbox、Agent Trace。
- `redis_data`：Redis AOF和LangGraph checkpoint。
- `rocketmq_broker_store`：Broker CommitLog和ConsumeQueue。
- `qdrant_data`：向量索引。
- `agent_model_cache`：本地Embedding模型缓存，避免重复下载。

不允许把API Key、JWT密钥或用户上传内容写入镜像层。

## 6. 环境变量

`.env.example`至少包含：

```dotenv
# Tested starting baseline; Day 7 records resolved content IDs in docs/image-manifest-day7.json
MYSQL_IMAGE=mysql:8.4
REDIS_IMAGE=redis:7.4-alpine
ROCKETMQ_IMAGE=apache/rocketmq:4.9.7
QDRANT_IMAGE=qdrant/qdrant:v1.12.5
MAVEN_IMAGE=maven:3.9.11-eclipse-temurin-21
JAVA_RUNTIME_IMAGE=eclipse-temurin:21-jre-jammy
PYTHON_RUNTIME_IMAGE=python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93
NODE_BUILD_IMAGE=node:22-alpine
NGINX_RUNTIME_IMAGE=nginx:1.27-alpine
UV_VERSION=0.10.9
APP_IMAGE_TAG=day7

# Database
MYSQL_ROOT_PASSWORD=__REPLACE_WITH_RANDOM_ROOT_PASSWORD__
BUSINESS_DB_NAME=meeting_business
BUSINESS_DB_USER=meeting_business
BUSINESS_DB_PASSWORD=__REPLACE_WITH_RANDOM_BUSINESS_PASSWORD__
AGENT_DB_NAME=meeting_agent
AGENT_DB_USER=meeting_agent
AGENT_DB_PASSWORD=__REPLACE_WITH_RANDOM_AGENT_PASSWORD__

# Redis
REDIS_PASSWORD=__REPLACE_WITH_RANDOM_REDIS_PASSWORD__
REDIS_URL=redis://:__REPLACE_WITH_RANDOM_REDIS_PASSWORD__@redis:6379/0
AGENT_CHECKPOINT_REDIS_URL=redis://:__REPLACE_WITH_RANDOM_REDIS_PASSWORD__@redis:6379/1

# RocketMQ
ROCKETMQ_NAMESRV_ADDR=rocketmq-namesrv:9876
ROCKETMQ_BROKER_NAME=meeting-broker

# Java security
JWT_SECRET=__REPLACE_WITH_AT_LEAST_32_RANDOM_BYTES__
AGENT_CONTEXT_JWT_SECRET=__REPLACE_WITH_ANOTHER_RANDOM_SECRET__
INTERNAL_SERVICE_TOKEN=__REPLACE_WITH_RANDOM_SERVICE_TOKEN__

# Agent
AGENT_MODEL_PROVIDER=fixture
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
MODEL_TIMEOUT_SECONDS=45
MODEL_MAX_RETRIES=2
AGENT_MAX_MODEL_CALLS=12
AGENT_MAX_TOOL_CALLS=16
AGENT_MAX_GRAPH_NODES=20
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=meeting_policies

# Service URLs
BUSINESS_SERVICE_URL=http://business-service:8080
AGENT_SERVICE_URL=http://agent-service:8000
VIDEO_PROVIDER_URL=http://video-provider-mock:8010

# Application
APP_TIMEZONE=Asia/Shanghai
APP_DEMO_DATA_ENABLED=true
APP_HOT_BOOKING_ENABLED=true
BOOKING_DRAFT_TTL_MINUTES=10
OUTBOX_PUBLISH_INTERVAL_MILLIS=500
OUTBOX_MAX_RETRIES=10
ROCKETMQ_BOOKING_TOPIC=meeting-booking
ROCKETMQ_DOMAIN_TOPIC=meeting-domain
ROCKETMQ_BOOKING_CONSUMER_GROUP=meeting-booking-finalizer
ROCKETMQ_RESULT_CONSUMER_GROUP=meeting-agent-result-callback
AGENT_CALLBACK_ENABLED=true
LOG_LEVEL=INFO
```

`AGENT_MODEL_PROVIDER=fixture` 是 Day 4/5 的确定性本地 Smoke/Test Provider；切换为 `deepseek` 时才允许调用配置的 OpenAI-compatible DeepSeek 端点。无论 Provider 选择如何，未配置 DeepSeek Key 时健康接口仍返回 HTTP 200 / `DEGRADED`。`agent-service` 同时接收 `INTERNAL_SERVICE_TOKEN` 和 `AGENT_CONTEXT_JWT_SECRET`，仅用于验证 Java 代理的内部上下文并在调用 Java 白名单 Tool 时透传，日志、Trace 和 SSE 中不得输出它们。`AGENT_CHECKPOINT_REDIS_URL` 必须指向 Redis 的隔离 DB 1，用于保留 24 小时的 LangGraph checkpoint；`REDIS_URL` 保留 DB 0 给其他 Agent Redis 用途。Day 5 以 `AGENT_CALLBACK_ENABLED=true` 为默认值，使 Java 的 `BOOKING_RESULT` 消费者能在事务提交后恢复等待中的 Agent Run。

要求：

- `.env`加入 `.gitignore`。
- README不得包含真实DeepSeek Key。
- 演示环境启动前自动检查默认密码和空密钥。
- 没有DeepSeek Key时，Java手动功能正常，Agent健康状态标记为degraded。
- `rocketmq-topic-init` 必须在 Broker 健康后幂等创建两个 Topic 和固定 Consumer Group，成功退出后 business-service 才启动；Broker 保持 `autoCreateTopicEnable=false` 和 `autoCreateSubscriptionGroup=false`。
- 固定的 `apache/rocketmq:4.9.7` 镜像使用 Java 8；在 Docker Desktop 的 cgroup v2 环境中，NameServer、Broker 和管理命令通过 `JAVA_OPT_EXT=-XX:-UseContainerSupport` 绕开旧 JDK 的 cgroup 探测缺陷。各进程已有显式 `-Xms/-Xmx` 上限，因此该兼容参数不会取消本项目的堆内存约束。

MySQL、Redis、RocketMQ和Qdrant的初始镜像基线采用经过兼容性验证的版本。DeepSeek默认模型按项目设计时官方API文档中的可用模型配置；所有模型名仍由环境变量覆盖。Day 7必须把实际成功验收的镜像内容摘要记录到README或发布清单，避免未来同名tag变化。

## 7. 数据库初始化与迁移

### 7.1 初始化脚本

MySQL首次启动执行 `00-create-databases.sh`：

1. 创建 `meeting_business` 和 `meeting_agent`。
2. 创建两个独立账号。
3. 分别授权各自数据库。
4. 设置utf8mb4和统一时区。

脚本从环境变量读取数据库名、账号和密码，再通过MySQL客户端创建。不得在初始化脚本中写真实密码；注意普通 `.sql` 文件不会自动替换Compose环境变量，因此这里明确使用 `.sh`。

### 7.2 Java迁移

- business-service启动时运行Flyway。
- DDL放在 `business-service/src/main/resources/db/migration`。
- 演示数据与结构迁移分离。
- 禁止使用ORM自动修改生产表结构。

### 7.3 Python迁移

- agent-service启动前运行Alembic upgrade。
- 失败时容器退出，不在不完整Schema上启动API。
- Agent checkpoint存放Redis，不依赖MySQL迁移才能恢复图状态；Agent Run元数据依赖MySQL。

### 7.4 RAG 初始化

RAG 使用一次性 `rag-init` Compose 服务完成受控文件导入；`agent-service` 只有在 `rag-init` 成功退出后启动：

1. `deploy/rag-documents/` 只读挂载到 `/app/rag-documents`，支持 UTF-8 Markdown 与文本型 PDF；PDF 不做 OCR。
2. `rag-init` 先执行 Alembic，再运行 `python -m app.rag.ingest --source-dir /app/rag-documents`，对 Front Matter、标题切片、checksum、`rag_document` 和 Qdrant payload 做校验与幂等写入。
3. 相同 checksum 的重复启动跳过；同 documentId 内容变化时替换该文档的完整向量集合；源文件消失不会自动删除索引。
4. Retriever 继续使用稳定 chunk ID 与确定性 hash embedding，不下载外部模型；版本内 4 条 `SEED_CHUNKS` 只保留为 fixture/向后兼容最小语料，真实文件 corpus 由 `rag-init` 写入同一 collection。
5. 任一文档导入失败使 `rag-init` 非零退出，`agent-service` 不在部分初始化状态下启动。Qdrant 运行期不可用时，Policy Agent 仍按既有降级规则返回无证据状态。

## 8. Compose骨架

以下是实现时必须遵循的结构示意；实际文件应补齐镜像固定版本、资源限制和安全选项：

```yaml
name: meeting-scheduler

services:
  mysql:
    image: ${MYSQL_IMAGE}
    env_file: .env
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      TZ: ${APP_TIMEZONE}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./deploy/mysql/init:/docker-entrypoint-initdb.d:ro
    networks: [backend_net]
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-p${MYSQL_ROOT_PASSWORD}"]
      interval: 5s
      timeout: 5s
      retries: 30

  redis:
    image: ${REDIS_IMAGE}
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${REDIS_PASSWORD}"]
    volumes:
      - redis_data:/data
    networks: [backend_net]
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20

  rocketmq-namesrv:
    image: ${ROCKETMQ_IMAGE}
    command: sh mqnamesrv
    networks: [backend_net]
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$(ps -ef | grep -v grep | grep NamesrvStartup)\""]
      interval: 10s
      timeout: 5s
      retries: 20

  rocketmq-broker:
    image: ${ROCKETMQ_IMAGE}
    command: sh mqbroker -n rocketmq-namesrv:9876 -c /home/rocketmq/conf/broker.conf
    depends_on:
      rocketmq-namesrv:
        condition: service_healthy
    volumes:
      - rocketmq_broker_store:/home/rocketmq/store
      - ./deploy/rocketmq/broker.conf:/home/rocketmq/conf/broker.conf:ro
    networks: [backend_net]
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$(ps -ef | grep -v grep | grep BrokerStartup)\""]
      interval: 10s
      timeout: 5s
      retries: 30

  qdrant:
    image: ${QDRANT_IMAGE}
    volumes:
      - qdrant_data:/qdrant/storage
    networks: [backend_net]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:6333/healthz >/dev/null || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 20

  video-provider-mock:
    build:
      context: ./mock-services/video-provider
    networks: [backend_net]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')"]
      interval: 5s
      timeout: 3s
      retries: 20

  business-service:
    build:
      context: ./business-service
    env_file: .env
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
      rocketmq-namesrv:
        condition: service_healthy
      rocketmq-broker:
        condition: service_healthy
    networks: [backend_net]
    expose: ["8080"]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8080/actuator/health/readiness >/dev/null || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30

  agent-service:
    build:
      context: ./agent-service
    env_file: .env
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_healthy
      business-service:
        condition: service_healthy
    volumes:
      - agent_model_cache:/models
    networks: [backend_net, agent_egress_net]
    expose: ["8000"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/internal/v1/health')"]
      interval: 10s
      timeout: 5s
      retries: 30

  frontend:
    build:
      context: ./frontend
    depends_on:
      business-service:
        condition: service_healthy
    ports:
      - "${FRONTEND_PORT:-80}:80"
    networks: [edge_net, backend_net]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost/health >/dev/null || exit 1"]
      interval: 10s
      timeout: 3s
      retries: 20

networks:
  edge_net: {}
  agent_egress_net: {}
  backend_net:
    internal: true

volumes:
  mysql_data: {}
  redis_data: {}
  rocketmq_broker_store: {}
  qdrant_data: {}
  agent_model_cache: {}
```

注意：某些基础镜像可能不包含 `wget` 或 `ps`。实现时必须基于实际固定镜像验证healthcheck，并使用镜像内可用命令或专用健康探针，不能原样复制后不测试。

## 9. 开发覆盖文件

`compose.dev.yaml`只用于本地调试：

```yaml
services:
  business-service:
    ports: ["8080:8080"]
  agent-service:
    ports: ["8000:8000"]
  video-provider-mock:
    ports: ["8010:8010"]
  mysql:
    ports: ["3306:3306"]
  redis:
    ports: ["6379:6379"]
  rocketmq-namesrv:
    ports: ["9876:9876"]
  rocketmq-broker:
    ports: ["10911:10911"]
  qdrant:
    ports: ["6333:6333"]
```

启动：

```powershell
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

## 10. Dockerfile要求

### 10.1 Java

- Maven多阶段构建。
- 构建阶段执行测试或由CI提前执行。
- 运行阶段只包含JRE和应用jar。
- 使用非root用户。
- 设置容器内存感知JVM参数。
- 暴露8080和Actuator readiness。

### 10.2 Python

- 使用slim基础镜像。
- 先复制依赖锁文件，利用Docker layer cache。
- 安装依赖后再复制源码。
- 使用非root用户。
- 模型缓存通过命名卷挂载，不打入镜像。
- API 进程内的确定性 Retriever 在首次政策检索时执行幂等 Qdrant seed；不需要额外的 RAG init 容器。

### 10.3 Frontend

- Node多阶段构建。
- 最终使用Nginx静态镜像。
- Nginx代理 `/api` 到Java，关闭对Python的直接代理。
- SSE路径禁用代理缓存并增加读取超时。

### 10.4 Mock服务

- 与Agent服务分离镜像，体现外部依赖边界。
- 数据可以仅保存在内存；容器重启后允许清空。
- 必须实现Idempotency-Key。

## 11. Nginx关键配置

Agent SSE需要：

```nginx
location /api/v1/agent/ {
    proxy_pass http://business-service:8080;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    add_header X-Accel-Buffering no;
}
```

其他 `/api/` 使用普通反向代理。SPA路由回退到 `index.html`。

## 12. 健康检查

### 12.1 Java readiness

必须检查：

- MySQL连接。
- Redis连接。
- RocketMQ生产者初始化状态。

Agent服务不可用只标记业务能力degraded，不必让Java readiness失败，以保证手动功能可用。

### 12.2 Python健康接口

返回：

```json
{
  "status": "UP",
  "deepseek": "UP",
  "businessService": "UP",
  "redisCheckpoint": "UP",
  "qdrant": "UP",
  "embeddingModel": "READY"
}
```

DeepSeek Key未配置时，`/internal/v1/health` 必须返回 HTTP 200，响应体 `status` 为 `DEGRADED`，以便容器保持可用；聊天接口返回明确配置错误。进程、数据库或基础启动依赖异常时才允许健康探针失败。

## 13. 启动与验收命令

### 13.1 首次启动

```powershell
.\scripts\New-LocalEnv.ps1
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

### 13.2 查看日志

```powershell
docker compose logs -f business-service agent-service
docker compose logs -f rocketmq-broker
```

### 13.3 健康检查

```powershell
Invoke-RestMethod http://localhost/health
Invoke-RestMethod http://localhost/api/v1/system/health
```

### 13.4 配置验证

CI和本地提交前必须运行：

```powershell
docker compose config --quiet
```

## 14. 资源建议

完整本地部署建议Docker Desktop至少：

- 8 CPU线程。
- 10 GB可用内存；本地Embedding首次加载时建议更多。
- 15 GB可用磁盘。

当前 `compose.yaml` 还声明了可由 Docker Compose 在本地引擎执行的硬上限：常驻服务合计最多约 **4.2 GiB RAM / 5.75 CPU**；一次性 RocketMQ 初始化容器最多使用 **384 MiB / 0.75 CPU**，一次性 `rag-init` 最多使用 **384 MiB / 0.50 CPU**。关键单服务上限为 MySQL、RocketMQ Broker、Java 和 Python 各 768 MiB；Qdrant、NameServer 和 `rag-init` 各 384 MiB；Redis 192 MiB；前端和 Video Mock 各 128 MiB。实际性能报告必须同时记录宿主机/Docker Desktop 分配，而不能把这些上限当作已测得使用量。

如果机器资源不足：

- 降低Java和RocketMQ堆大小。
- 先单独启动infra，再在宿主机运行应用。
- 不删除Qdrant，RAG是验收范围。
- 可延迟加载Embedding模型，但第一次检索会变慢。

## 15. 演示环境安全

- 所有默认密码在启动前替换。
- 只发布frontend端口。
- 容器使用非root用户。
- Java和Python内部API验证Service Token。
- 限制容器日志大小并轮转。
- 不在日志打印Prompt中的敏感个人信息全文。
- 演示数据使用虚构员工和会议。

## 16. CI中的Docker任务

1. 校验Compose语法。
2. 构建四个应用镜像。
3. 启动MySQL、Redis、RocketMQ和Qdrant。
4. 执行Java集成测试和Python测试。
5. 启动完整Compose。
6. 运行健康检查和Golden Path smoke test。
7. 失败时收集应用日志。
8. 关闭容器。
