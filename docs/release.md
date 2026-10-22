# Android 构建与发布

现有 Expo 云构建流程继续使用，入口见 [维护说明](maintainers.md)。审核预览先连接本机隧道，正式发布才使用生产地址，参见 [本地审核](local-development.md)。本批增加了 BLE 原生模块，必须重新构建 Android 包；JS 导出成功不能代替原生编译和真实手环验收。

## 本地 Gradle 构建

使用团队原有签名和 Firebase Android 配置，不要生成新签名替换已有安装链。配置文件与密码放在仓库外，不能提交。

```bash
export ANTANG_KEYSTORE=/path/to/antang-release.keystore
export ANTANG_KEYSTORE_PASSWORD_FILE=/path/to/antang-release.properties
export EXPO_PUBLIC_API_URL=https://本次选定的地址
bash scripts/build-android-local
```

properties 包含 `storePassword`、`keyAlias`、`keyPassword`。构建脚本将 `scripts/android-version --apply` 的单调版本号写入 app.json，prebuild 和 Gradle 使用同一明确地址。已有 EAS 流程仍沿用其远端版本管理；不要在两个渠道混发未经核对的版本和签名。

**环境由地址决定，别手写。** `apps/mobile/app.config.js` 按 `EAS_BUILD_PROFILE` 决定应用名与 `appEnvironment`（production / preview / development），而 `apps/mobile/src/config.ts` 在模块加载时就会检查：「非 production 环境 + 生产地址」直接抛错——包会一开就闪退。`scripts/build-android-local` 因此不再依赖外部变量：`EXPO_PUBLIC_API_URL` 指向 `82.157.48.200` 时强制 `EAS_BUILD_PROFILE=production`，指向本机隧道时保持 `development`；显式写 `production` 却给了非生产地址会直接退出。构建输出会打印实际使用的 profile，打包日志里对不上就是配错了。

## GitHub Actions 构建

`.github/workflows/android-release.yml` 为手动或 `v*` 标签触发。目标仓库需配置下列 Secrets，不能假设 fork 的配置会自动继承：

| Secret | 用途 |
| --- | --- |
| `ANTANG_KEYSTORE_BASE64` | 团队签名文件 |
| `ANTANG_KEYSTORE_PASSWORD` | 密钥库密码 |
| `ANTANG_KEY_PASSWORD` | 签名密码，别名沿用 antang |
| `ANTANG_API_URL` | 本次明确选择的 API 地址 |
| `ANTANG_GOOGLE_SERVICES_BASE64` | 现有 Firebase Android google-services.json |

先在 app.json 确认并提交新 versionCode，再触发云构建。构建只产生 artifact，不会自动上传服务器或影响存量用户。

## 更新检查与分发

患者侧边栏的「检查更新」读取所选后端地址的 `/download/latest.json`，比较 versionCode，确认后可在应用内下载并交给 Android 系统安装器；权限不足或安装器启动失败会明确报错。清单不可用会明确报错。

本仓库没有可执行的 APK 上传发布脚本；不能根据旧分支文档中的 `publish-apk.ps1` 假设它存在。由维护者先核对 APK 的签名、包名、versionCode、API 地址和 SHA256，再上传审核过的文件与清单。清单字段为 `version_name`、`version_code`、`apk_url`、`sha256`、`size_bytes`、`notes`；清单版本必须与 APK 一致且大于已发布版本。Android 安装仍由用户确认。

作者后续更新增加了 `expo-intent-launcher` 安装器入口和安装未知应用权限。缓存下载目前只核对文件大小，端上未计算 SHA256；发布端仍需校验 APK 哈希和团队签名。这些改动需要重新编译安装包，尚未真机验收。
