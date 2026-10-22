# Image-Embedding
图像检索

## Nacos 配置环境

通过环境变量 `NACOS_ENV` 选择 Nacos 环境：未设置或为空时默认 `prod`，
设置为 `test` 时只读取 test 配置。启动时如果所选 Nacos 环境不可用、配置为空或
配置解析失败，则直接读取本地 `backend/config/param/config.yaml`，不再尝试其他环境。
本地 YAML 也必须存在且有效。轮询同样只读取所选环境。

Docker Compose 已透传该变量，可在 `backend/.env` 中设置：

```dotenv
NACOS_ENV=test
```

也可直接将 `backend/docker-compose.yml` 中的 `NACOS_ENV` 值改为 `test`。
未设置时保留默认 prod。修改后重新创建容器：

```shell
docker compose -f backend/docker-compose.yml up -d --force-recreate
```

使用包含这些代码的镜像后，环境选择逻辑才会生效。
