# 随心记应用图标

聊天气泡与心形书签融合，表达「随心聊，轻松记」。森林绿搭配米白与鼠尾草绿，强调轻松、温暖的生活记录体验。

- `app-icon.png`：1024 × 1024 完整图标，用于应用品牌展示。
- `app-icon-foreground.png`：1024 × 1024 完整前景图，保留干净的完整构图，由鸿蒙系统按适配形状裁切；没有使用有杂点的透明抠图版本。
- `app-icon-background.png`：鸿蒙背景层。
- `app-icon-{72,96,144,192}.png`：Android 各密度图标，Android 构建尚未验证。
- Web 使用同源 `frontend/src/assets/brand.png`，作为侧边栏 Logo、欢迎图和 favicon。

2026-10-18 使用内置 imagegen 生成，sips 仅用于等比缩放。最终采用的生成提示词：

> Use case: logo-brand. Create ONE finished 1024x1024 square app launcher icon for 随心记, a warm personal life memory app: 随心聊，轻松记。Through chatting record life, automatically organize it, find it later. A newly designed distinct simple iconic symbol: a single warm ivory rounded conversation bubble whose flowing lower tail curls inward into a subtle heart-shaped bookmark stroke, suggesting chatting, care and remembered moments. One tiny muted sage accent tucked into the heart curve. Flat vector-like polished brand mark, bold simple continuous geometry, instantly legible at 32px. Solid full-bleed forest green background exactly #285E4A. Ivory #F7F8F4 and sage #A9C6B5 mark. Centered mark within central 64% of canvas with ample margins for HarmonyOS circular adaptive crop. No letters, no words, no text, no outer rounded-corner mask, no border, no shadows, no gradients, no 3D, no mockup, no extra symbols. Final production icon, not a concept sheet.
