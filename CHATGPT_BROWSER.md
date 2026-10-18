# 普通 ChatGPT 聊天：浏览器版安装

这版用于你电脑上的 Chrome / Edge 普通 ChatGPT 网页聊天。安装位置是**浏览器扩展管理页**。

**[下载普通聊天浏览器版 ZIP](https://github.com/tc06260000-bit/-/raw/refs/heads/main/dist/chatgpt-share-browser.zip)**

## 安装

1. 下载 ZIP，并在电脑上解压。
2. Chrome 地址栏输入 `chrome://extensions`；Edge 输入 `edge://extensions`。
3. 打开该页面的「开发者模式」。
4. 点击「加载已解压的扩展程序」，选择解压后**直接包含 manifest.json** 的 `chatgpt-share-browser` 文件夹。
5. 刷新 ChatGPT 网页。看到左下角「分享原文核对：已就绪」后使用。

不需要 Python、Skills、Codex、API 密钥或 ChatGPT 的开发者模式。这个 ZIP 不需要上传到聊天。

## 用法

在接收分享链接的账号，打开普通 ChatGPT 网页聊天，粘贴一个完整的 `https://chatgpt.com/share/…` 链接，照常按 Enter 或点击发送。也可以在链接后加问题。

扩展会先读取分享页、核对支持的可见文本，再把原文、消息条数和引用编号一起填入输入框，并触发一次发送。读取失败时会说明原因，不自动发送裸链接。若显示「未保护」，不要依赖本扩展。

## 核验情况

43 项解析测试和 25 项真实 Chromium 中的合成页面行为测试通过。本次云端 Chromium 禁止加载本地扩展，真实分享页也受到网络策略限制，因此**尚未完成用户真实 ChatGPT 页面中的扩展安装和端到端验证**。这不是保证模型零幻觉的工具。

扩展只访问 ChatGPT 同源分享页，请求可能使用你当前的 ChatGPT 登录会话；不读取 Cookie 值，不向第三方上传资料。超过 60000 字符的完整提示词或含无法读取的附件时会停止，不截断。网站改版或未知发送入口可能超出拦截范围。

[完整说明与范围](browser-extension/chatgpt-share-browser/README.md) · [测试与限制](browser-extension/chatgpt-share-browser/VALIDATION.md) · [扩展源码](browser-extension/chatgpt-share-browser/)

备用油猴脚本：[查看完整源码](dist/chatgpt-share-guard.user.js)。扩展版与油猴版只启用一个。

支持 Skills 的其他客户端仍可使用 [原 Skills 版](CHATGPT_SHARE_SKILL.md)。
