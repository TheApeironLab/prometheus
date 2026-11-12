# Prometheus 独立受保护应用

部署包版本 0.1.0；Prometheus 3.16.0，oauth2-proxy 7.15.6，Linux AMD64。
首版基于固定官方发行镜像，而非声称从 fork 源码编译。
状态：development / installation-not-run。无集群实装、SSO 或持久化验收结论。
不包含 Grafana、Alertmanager、exporter、Prometheus Operator 或集群自动发现。

## 构建

依赖 Python >=3.12、PyYAML、Helm 3、Skopeo、Git。保留上游 Makefile 的
build/test/lint 语义；`make deployment-test` 检查部署适配。

    make images MATERIALS=/absolute/new/materials
    make check MATERIALS=/absolute/new/materials OUTPUT=/absolute/new/check
    make deployment-test
    make package VERSION=0.1.0 MATERIALS=/absolute/new/materials OUTPUT=/absolute/new/package
    make verify PACKAGE=/absolute/new/package/prometheus-0.1.0-amd64-full.tar.gz ARCHIVE_SHA256=<trusted-sha>

所有输出目录必须不存在，不覆盖历史材料；images 从官方固定 tag 获取，记录实际
单平台 OCI manifest 和所有 blob 摘要，后续封包使用锁定的本地材料。
package 写归档同时计算 SHA256；verify 是显式深度复验。
首版没有基包，package-slim 明确拒绝执行。

## 安装

1. 从可信渠道取得外层 SHA256；使用随交付提供的 prepare_install.py：

       python3 prepare_install.py PACKAGE.tar.gz --sha256 TRUSTED_SHA --output NEW_DIRECTORY

   不从未经认证的归档提取脚本后直接执行。外层校验通过后安全解包，不重复扫描镜像。
2. 在 Vasi 导入 `vasi-package.json` 声明的 Chart 和两张镜像，保留 Vasi 自身的摘要、
   导入及部署校验。按当前 Vasi UI 的包导入流程操作；本工具不持有管理员凭据。
3. 建立独立 namespace `monitoring`。在 IAM 建立 confidential OIDC client，启用
   Authorization Code 与 S256；准确登记 https://HOST/oauth2/callback。配置 groups
   scope/claim，并只给获准用户分配 site.allowedGroups 声明的组。此版不自动配发 IAM client。
4. 在该 namespace 独立配发 site.oidcSecret：client-secret 和 cookie-secret；后者为
   32 个随机字节的 base64 表示。凭据不写 values、Git、分发包或日志。
5. 复制 site.example.yaml 为私有现场 values，调整域名、issuer、客户端、组、TLS 与
   ingress 网络选择器。私有 IAM CA 可通过 site.caSecret 引用含 ca.crt 的独立 Secret；禁止跳过 TLS 校验。
   若 IAM HTTPS 不是 443，显式补充 extraEgress。
6. 将 images.prometheus / images.oauth2Proxy 覆盖为 Vasi 导入后显示的现场仓库摘要
   引用；不要假设隔离目标能访问 quay.io。通过 Vasi 预览并安装 Chart（release 名建议
   prometheus，namespace monitoring），检查部署、证书、PVC 和代理日志。
   管理员排障也可 `helm upgrade --install prometheus charts/*.tgz -n monitoring -f PRIVATE_VALUES`。
7. 检查匿名访问跳转 IAM、有组用户成功、无组用户拒绝、Pod IP:9090 不可直连、
   /api/v1/status/config 中无管理接口开启、重启后 TSDB 保留。未完成前不标记实装通过。

## 边界与升级

Prometheus 只监听 loopback；Service/Ingress 仅连接代理。Kubernetes 抓取不继承
用户或执行器身份，默认不挂载 SA token、无集群 RBAC。默认仅抓取自身；增添目标时
需同时配置 scrapeConfig 与 extraEgress。默认允许 HTTPS 出站用于 IAM。
访问策略是 IAM groups + 代理检查；不是 Vasi 细粒度应用角色自动映射。
API 当前仅支持代理浏览器会话，不承诺 Vasi 委托令牌直接调用或自动化 API 身份。
关闭管理、生命周期及 remote-write 接收接口。保留代理 cookie 默认大小限制；组很多
时应评估独立会话存储，首版未包含 Redis。

PVC 默认保留，升级采用单副本 Recreate，存在短暂不可用。升级前备份数据，保留
相同 release/namespace/PVC、OIDC client 与 cookie Secret；Secret 轮换后显式重启
Deployment。本版不支持多副本 HA、自动扩容、自动回滚或 TSDB 降级兼容承诺。
