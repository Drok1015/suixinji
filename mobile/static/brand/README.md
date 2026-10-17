# 随心记应用图标

两个相扣的对话气泡组成记忆纽带，表达「聊过的，记得」。深森林绿沿用应用主色，米白与鼠尾草绿表达温暖、安静的私人陪伴。

- `app-icon.png`：1024 × 1024 完整图标，用于应用内品牌展示。
- `app-icon-foreground.png`、`app-icon-background.png`：1024 × 1024 鸿蒙分层图标，已配置在 `manifest.json`。
- `app-icon-{72,96,144,192}.png`：Android 各密度图标，已配置，尚未编译 Android。

采用内置 imagegen 工具生成；源图先保存至 Codex generated_images，再复制到本目录。密度版本使用 sips 转换，纯色背景由脚本生成。

生成提示词：

> Use case: logo-brand. Create ONE finished square app launcher icon for 随心记, a private AI companion that chats with you and remembers your life, plans and preferences. Minimal timeless flat vector-like symbol: a warm ivory continuous rounded ribbon forming two interconnected conversation bubbles, subtly suggesting an infinity memory knot, with one clearly readable chat tail. Friendly, calm, intimate, elegant, recognizable at 48px. Deep forest green #285E4A solid full-bleed square background, ivory #F7F8F4 main symbol with a restrained soft sage #A9C6B5 interlocking accent. Centered large bold simple symbol occupying about 65% of canvas, generous 16% safe margins. Exactly one icon, front-facing square artwork filling canvas edge-to-edge. No outer rounded corner mask, no border, no typography or letters, no watermark, no mockup or device, no shadows or 3D, no tiny details. 1024x1024.

透明层编辑提示：移除绿色背景和气泡内绿色，保留米白及鼠尾草绿符号；清理边缘与杂点，保持透明通道和安全边距。

鸿蒙图标规范：https://uniapp.dcloud.net.cn/tutorial/harmony/runbuild

2026-10-05：通过 HBuilderX Web 预览和 Playwright 模拟接口数据检查主页无新增按钮、向右箭头、卡片进入聊天、登录及欢迎页图标加载。截图保存在 `docs/qa/mobile-home-entry.png`、`mobile-icon-login.png`、`mobile-icon-chat.png`。同日已重新构建 0.2.1 / 201、签名验证并上传蒲公英；桌面图标仍需在真机安装后验证。
