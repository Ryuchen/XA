# Compose 编排

以下命令从仓库根目录执行。所有生产环境变量均由维护者通过私有环境文件提供，不提交密钥。

| 文件 | 用途 |
| --- | --- |
| `docker-compose.remote.yml` + `docker-compose.release.yml` | 自带 MySQL 的发布编排与正式环境覆盖配置 |
| `docker-compose.managed.yml` | 外部 MySQL 发布编排，不与自带数据库的文件叠加 |
| `docker-compose.yml` + `docker-compose.web.yml` | 历史容器联调配置；当前日常开发使用 `deploy/dev.ps1` |

正式发布通过 `deploy/deploy-release.sh` 选择数据库模式，并从 `deploy/release.env.example` 或 `deploy/managed.env.example` 准备私有配置。发布脚本要求明确的源码提交和镜像标签，不生成真实凭证。三端静态镜像分别使用 `boss`、`provider`、`admin` 构建参数，并由 `deploy/Caddyfile` 按门店域名和路径路由。

平台 Logo 由 Caddy 从共享媒体卷的 `platform/logos` 提供。门店的公开素材使用本店域名，网关将原 Host 交给 Django，沿用门店目录及存储根。已保存的共享媒体域公开链接会临时跳转到已登记的本店域名。私有媒体通过原有签名接口读取和续签。动态域名上线仍需维护者配置网关站点和 `DJANGO_ALLOWED_HOSTS`。

本目录中的文件移动只改变仓库路径，不执行数据库迁移或部署。配置渲染、镜像构建和静态路由通过不能替代目标环境中的数据库、授权、资金及真实渠道验收。
