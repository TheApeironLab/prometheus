# Prometheus Operator 组件 0.2.1

状态：development / installation-not-run。应用适配版本 0.2.1，Prometheus 3.16.0，
Operator/config-reloader 0.95.1，oauth2-proxy 7.15.6，查询代理 0.1.1，Linux AMD64。
仅封装构建与契约检查不代表集群安装、SSO、升级或 TSDB 迁移验收通过。

## 构建和校验

使用仓库 Makefile；Python、PyYAML、Helm、Skopeo、Docker、Git 为构建依赖。

    make images VERSION=0.2.1 MATERIALS=/absolute/new/materials BASE_MATERIALS=/absolute/previous/materials
    make deployment-test
    make package VERSION=0.2.1 MATERIALS=/absolute/new/materials OUTPUT=/absolute/new/package
    make verify PACKAGE=/absolute/new/package/prometheus-0.2.1-amd64-full.tar.gz ARCHIVE_SHA256=TRUSTED_SHA

BASE_MATERIALS 可选；只有来源、源码及 OCI 摘要匹配的镜像才复用。五种镜像完整纳入包。
外层 SHA256 在压缩时计算；显式 verify 安全解包并检查文件及 OCI 闭包。不覆盖旧输出。

## 平台前置与 Vasi 安装

1. 从可信渠道取得外层 SHA256，使用交付的 prepare_install.py 验证后安全解包。
2. 平台管理员审阅 operator/source.json 和 crds.yaml。在原 CA 验证的管理员上下文运行：

       python3 operator/prepare.py --kubeconfig /private/site-kubeconfig
       python3 operator/prepare.py --kubeconfig /private/site-kubeconfig --apply

   前一条只做 server dry-run；后一条再应用并等待 CRD Established。不会 force-conflicts。
   CRD 是集群共享平台资源，不能跟应用卸载自动删除。已有 Operator 的站点必须先检查
   CRD 版本及 namespace 管理范围，避免两个控制器接管同一实例。本版使用 namespace.release 的 controller-id，
   显式标记自己管理的 Prometheus CR；只部署专用 namespace。
   包中固定的十种 CRD 属于该版本控制器 API，不代表启用十种服务；不部署 Grafana、
   Alertmanager 或 Thanos。CRD 大文件采用 server-side apply，避免客户端 annotation 限制。
3. 使用 Vasi 正常流程导入 vasi-package.json 中的五镜像和 Chart。
   CRD 前置文件不伪装成普通应用 Chart。执行器需要 monitoring namespace 内的
   Prometheus CR、ServiceAccount、Role/RoleBinding、Deployment、Service、Ingress、
   NetworkPolicy 管理权限，以及 Chart 声明的两个具名集群 RBAC 对象的创建/bind 权限。
   由平台管理员审阅并授权；不得自动提权或给 Vasi Web 增加管理员凭据。
4. 准备 site.example.yaml；配发 OIDC client（Authorization Code + S256）、限定组、
   OIDC Secret(client-secret/cookie-secret)、原 CA Secret、HTTPS 域名与证书。
   原凭据不进入包或 values；不得关闭 TLS 校验。query.enabled=true 时另配
   query.authSecret(token) 与 query.tlsSecret(tls.crt/tls.key)。
5. Vasi 预览后安装 namespace monitoring、release prometheus。检查 Operator Deployment、
   Prometheus CR 状态、它生成的 StatefulSet/Pod、PVC、SSO 及真实查询；不能仅凭
   Helm 命令退出 0 判定完成。Operator 仅管理当前 namespace。

## 采集和权限

GPU 组件与 Prometheus 放在同一监控 namespace；GPU ServiceMonitor 标签
`monitoring.apeironlab.cn/instance: prometheus` 必须匹配 release 名。
ServiceMonitor/PodMonitor 在本 namespace 发现 Service/Pod；ignoreNamespaceSelectors=true
阻止 Monitor 扩大目标 namespace。本版不支持跨 namespace 采集，后续须显式扩展 RBAC。
Monitor 创建权限只授予受信任维护者；标签是选择条件，不是身份授权。
禁止任意容器文件引用；采集认证及 CA 使用 Monitor 中的 Secret key 引用。
Prometheus SA 仅有本 namespace 服务发现读权限，不读 Secret；Operator 按其工作职责
读取本 namespace Secret、管理配置和 StatefulSet。集群权限只读 namespace 元数据和
StorageClass。不会采集 kubelet、自动创建 Node 权限或安装 GPU 驱动。

采集需要在 networkPolicy.extraEgress 显式允许 exporter 的 namespace/Pod/端口；
exporter 的入口策略同时限定 Prometheus Pod。内置 DNS/API/HTTPS 出站不替代采集授权。
默认 Prometheus 仅监听 loopback，remote-write receiver 和 admin API 关闭；Operator
配置重载使用内部生命周期端点，仅本 Pod 可达，查询代理不放行该端点。

## Vasi 查询绑定

保留 `https://RELEASE-query.NAMESPACE.svc:9443`、原 CA 和独立 Bearer Secret 契约。
query.allowedNamespace/allowedPodLabels 共同约束来源；浏览器 IAM 会话和机器 token
分开。查询代理只接受 GET query/query_range，拒绝写入、admin、reload，凭据不传浏览器。
Vasi 继续自身用户授权，不访问 Operator 或使用其身份。

## 从 0.1.x 重装（不保留历史指标）

可以卸载旧 Prometheus release，再按旧 PVC 的实际 namespace/name/UID 单独删除其专用数据卷。
旧 Chart 的 keep 策略会留下 PVC，不能仅卸载后认为数据已清理；禁止按 namespace 批量删卷。
原 IAM client、OIDC Secret、查询 Secret、CA 和域名复用；不删除 exporter 或其他应用数据。
然后先准备 CRD，以原 release/namespace 安装本版，验证 Operator/StatefulSet/PVC、认证、
ServiceMonitor 实际目标和 Vasi 查询。此流程会清空历史指标；实装尚未验收。

## 从 0.1.x 迁移（需要保留历史指标）

这是 Deployment 到 Operator StatefulSet 的架构变更，禁止直接覆盖旧 release。
用新 release 名/PVC 并行准备，保留旧 Deployment/PVC、OIDC 及查询 Secret。
需要历史 TSDB 时先停写并备份，在原 Prometheus 版本下离线迁移到新 PVC，禁止双写同卷。
核验历史查询、重启数据、认证后再切换 Vasi 查询绑定；旧现场保留作回退。
本包不自动复制数据、不自动删除旧卷、不承诺 TSDB 降级兼容。新 PVC 默认 Retain。
CRD 升级单独审阅后 server-side dry-run/apply，不删除重建 CRD；控制器镜像和 CRD
版本必须同步。实际迁移和控制器重启恢复本轮尚未验收。
