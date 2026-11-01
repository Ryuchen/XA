# SSH 自动部署故障排查

`Permission denied (publickey)` 表示 SSH 服务器未接受当前账号的密钥；它不是 Docker Hub 登录失败。`ssh-agent` 成功加载私钥也不代表服务器已授权该公钥。密钥注释不匹配 GitHub URL 的提示只影响 GitHub 仓库专用密钥识别，不是登录业务服务器失败的根因。

## 2026-10-23 的已确认结果

- 主分支提交：`72570635d4e839f9777a76ea9860a3f1a065906b`。
- [CI](https://github.com/Ryuchen/XA/actions/runs/39955522754) 通过。
- [镜像与部署运行](https://github.com/Ryuchen/XA/actions/runs/39955522743) 的 backend、dispatch、provider 三个镜像构建、发布与证明均通过。
- [部署任务](https://github.com/Ryuchen/XA/actions/runs/39955522743/job/120423100943) 在第一次 SSH 连接报 `Permission denied (publickey)`，退出码 `255`。远程部署脚本尚未运行，不能将镜像发布成功解释为生产更新成功。

## 管理员核对顺序

1. 在 GitHub 仓库 Actions Secrets 核对 `DEPLOY_HOST`、`DEPLOY_USER` 是否指向实际部署服务器和系统账号。不要把主机地址、密码或私钥复制到 issue、日志或聊天中。
2. 在已获授权的安全终端或服务器控制台，比较部署密钥的公钥指纹与该账号 `authorized_keys` 中的指纹。核对账号状态、家目录与 SSH 文件所有权/权限，以及服务端 SSH 日志。不要仅依据密钥注释判断是否匹配。
3. 若需要更换 Secret、生成密钥、增删 `authorized_keys` 或修改 SSH 配置，先获得相应授权；不要覆盖其他管理员的密钥，不要为排障开启密码登录或关闭主机密钥检查。
4. 修复确认后，重跑上述运行的失败部署任务即可复用已经发布的完整 SHA 镜像；不必先重建所有镜像。重跑会执行真实部署，必须有部署授权。
5. 确认部署任务通过，并核对服务器源码 SHA、Compose 健康状态、数据库检查及实际 API / 后台 / 陪玩端访问。SSH 预检通过仅证明能登录，不代表应用部署或业务验收通过。

## 脚本行为

`deploy/deploy-remote.sh` 先执行无副作用的 SSH `true` 预检，再通过标准输入进行 Docker Hub 登录，最后传输 `deploy-release.sh`。预检失败不会传送 Docker Hub Token，也不会开始部署；登录或部署失败会保留非零退出码。使用批处理模式和有界连接超时，保留原有 `StrictHostKeyChecking=accept-new` 策略。

该改动只改善失败定位和可测试性，**不会修复或绕过服务器密钥授权**。相关凭证清单见 [INTEGRATION_CREDENTIALS.md](INTEGRATION_CREDENTIALS.md)。

本地回归（仅使用模拟 SSH/Docker，不连接生产）：

```bash
bash -n deploy/deploy-remote.sh deploy/deploy-release.sh
node --test scripts/test-deploy-remote.mjs scripts/test-deploy-release.mjs
```
