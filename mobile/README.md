# 随心记 · 手机端

随心聊，轻松记。通过聊天记录生活，自动整理，在需要时找回。

基于 uni-app x，使用 `.uvue` + UTS。目前维护鸿蒙端；Android 配置保留，尚未验证构建。Web 项目在根目录 `frontend/`。

## 开发

使用 HBuilderX 5.26 或更新版本打开本目录，运行到鸿蒙设备或模拟器。构建前配置自己的 DCloud AppID、鸿蒙包名及签名证书。实际设备安装与体验需单独验证。

- `common/api.uts`：手机端后端地址，按自己的部署调整。
- `vite.config.js`：H5 预览代理，按自己的测试服务调整。
- `manifest.json`：应用名称、版本、图标和平台配置。
- `pages.json`：页面与底部导航配置。

运行到 Web 后，可在 `http://127.0.0.1:5174/` 预览；Web 预览不能替代鸿蒙真机验证。

## 功能

底部三栏：对话、记忆、我的。支持历史会话、流式聊天、停止与重试、长按复制、记忆编辑与来源查看、分类管理及待归类处理。

当前免登录，与同一后端连接的客户端访问同一份数据；请自行限制服务访问范围。

Markdown 使用原生组件展示，支持标题、列表、引用、代码、表格等。HTML 按普通文字处理，链接只接受 HTTP / HTTPS。

## 验证

在仓库根目录执行：

```sh
node scripts/test-mobile-sse.cjs
node scripts/test-mobile-markdown.cjs
```

SSE 和 Markdown 测试可独立运行；浏览器回归需另行启动预览服务、配置 Playwright，并使用隔离测试数据。

## 签名与分发

证书、私钥、Profile、设备标识和分发平台 Key 只保存在本地私密目录，不得提交。生成的安装包同样不进入源码仓库。包名与签名需保持一致，升级时增加 versionCode。

`scripts/publish-harmony.py` 从根目录 `.credentials-pgyer.json` 读取分发凭据。上传与更新元数据激活是两个步骤；完成签名验证及分发配置后再激活，后端部署后才会提示新版本。

App 从后端 `/api/releases/harmony` 检查版本，新版本由用户确认后打开分发页下载安装，并非静默安装。
