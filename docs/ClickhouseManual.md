## ClickHouse 快速部署指南

### 一、快速启动

#### 方式1：使用项目脚本（推荐）

```bash
# 进入项目目录
cd ~/programs/Log_Analysis_AI_Assistant

# 运行脚本，选择选项 3 启动服务
./tests/collectors/setup_project.sh
# 输入: 3
```

#### 方式2：手动启动

```bash
# 启动容器
docker compose -f tests/collectors/docker-compose-full.yml up -d

# 验证状态
docker ps | grep clickhouse
```

### 二、连接测试

```bash
# 使用默认用户登录
docker exec -it clickhouse-server clickhouse-client

# 使用项目用户登录
docker exec -it clickhouse-server clickhouse-client \
  --user your_user_here --password your_password_here
```

### 三、用户配置

```bash
# 进入客户端
docker exec -it clickhouse-server clickhouse-client

# 创建用户（首次启动）
CREATE USER IF NOT EXISTS your_user_here IDENTIFIED BY 'your_password_here';
GRANT ALL ON log_analysis.* TO your_user_here;
SHOW GRANTS FOR your_user_here;
```

### 四、常用命令

| 操作 | 命令 |
|------|------|
| 启动服务 | `docker compose -f tests/collectors/docker-compose-full.yml up -d` |
| 停止服务 | `docker compose -f tests/collectors/docker-compose-full.yml down` |
| 查看日志 | `docker logs clickhouse-server --tail 20` |
| 进入客户端 | `docker exec -it clickhouse-server clickhouse-client` |
| 带用户连接 | `docker exec -it clickhouse-server clickhouse-client --user lingluody --password yy63480096` |

### 五、端口信息

| 服务 | 端口 |
|------|------|
| ClickHouse HTTP | 8123 |
| ClickHouse Native | 9000 |
| Kafka | 9092 |

### 六、故障排查

```bash
# 检查端口
curl http://localhost:8123/ping

# 查看容器状态
docker ps

# 查看日志
docker logs clickhouse-server
```