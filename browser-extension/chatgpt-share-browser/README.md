# 普通 ChatGPT 网页版：分享原文核对扩展

**安装到电脑 Chrome / Edge 浏览器的扩展管理页。** 适用于普通 `https://chatgpt.com/` 聊天，不要求 Skills、Codex、开发者模式 MCP 或 API 密钥。

## 安装

1. 下载 `chatgpt-share-browser.zip`，在电脑上先解压。
2. Chrome 地址栏输入 `chrome://extensions`；Edge 输入 `edge://extensions`。
3. 打开该扩展管理页面的 **开发者模式**。
4. 点击 **加载已解压的扩展程序**，选择解压后**直接包含 `manifest.json`** 的 `chatgpt-share-browser` 文件夹。
5. 刷新 ChatGPT 网页。在左下角看到“分享原文核对：已就绪”后使用。

官方安装参考：[Chrome](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world)、[Edge](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading)。

这里的浏览器“开发者模式”只是加载本地扩展的开关，与 ChatGPT 的开发者模式 / MCP 无关。ZIP 不需要上传到聊天。

## 使用

在**接收分享链接的账号**打开普通 ChatGPT 聊天。建议使用新聊天，给原文留出上下文空间。

像平时一样粘贴一个完整链接并发送：

```text
https://chatgpt.com/share/你的分享ID
```

也可以附上问题：

```text
https://chatgpt.com/share/你的分享ID
我们最后确定了哪些要求？请引用对应原句。
```

扩展先拦住本次发送，读取分享页数据、核对当前分支和支持的可见文本，然后把含原文、条数和消息编号的提示词填入输入框，触发一次发送。你不用手动导出或上传 Markdown。

失败时不会把这次裸链接自动交给模型猜答。请看左下角原因。若显示“未保护”，说明当前输入框或发送按钮没有被识别，这时不要依赖本扩展。

成功触发后仍应看聊天界面确认消息是否已接收。扩展只触发一次发送，不自动重试，以免重复发消息。

## 回答如何核对

发给模型的指令要求它先报告实际收到的条数、首尾消息的短原句，并用 `[T0001]` 这样的编号支持关键结论；没有原文依据时明确说明。

这是回答约束，**不能保证模型零幻觉**。消息数、首尾原句和文件摘要都不能单独证明模型读完了全部内容。浏览器只能提供原文和请求，无法强制改变模型推理或上下文容量。

## 支持范围和停止条件

- 电脑 Chrome / Edge 的 `chatgpt.com` 普通聊天网页。手机 App 不支持这种安装方式。
- 自动处理一条消息中的一个公开分享链接；多个分享链接时先停止。
- 支持已识别的 textarea、ProseMirror、Lexical 和近期 ChatGPT composer 选择器；网页界面变化可能使其失效。
- 识别点击发送、输入框 Enter，以及关联表单 submit。Shift+Enter、中文输入法选词不触发；未知发送方式不承诺拦截。
- 分享页返回验证页、登录页、403、未知结构、缺失父节点、分支循环、未完成消息、图片、附件、音视频等，均停止。
- 发送这条链接时请不要同时附加文件。已识别的非文本预览也会停止；无法识别的未来网页 UI 不承诺覆盖。
- 默认总提示词上限为 60000 个 JavaScript 字符单位；超过上限就停止，不截断。这个限制不代表模型上下文一定够用。
- 只核对当前分享快照的支持文本，不包含原账号未分享、未更新的历史或隐藏内容。
- 抓取期间修改输入、换聊天或输入框重建会取消本次操作，避免覆盖新草稿。

## 数据与权限

Manifest 只在 `https://chatgpt.com/*` 主页面注入脚本，没有第三方服务、API 密钥、遥测、额外主机权限或持久存储。

浏览器对 ChatGPT 分享页做同源请求，**可能正常使用当前 ChatGPT 登录会话**。代码不读取 Cookie 值、不把凭证交给第三方；也不访问原文中嵌入的其他网址。提取内容只保留在当前标签页内存，随后通过你原本的聊天输入框交给 ChatGPT。可点击“保存已提取原文”另存一份本地文本。

只安装本目录扩展或备用油猴脚本中的一种，避免两个版本重复接管同一输入框。移除可在浏览器扩展管理页完成。

## 测试和未验证事项

见 `VALIDATION.md`。合成页面测试与本地扩展加载测试不能代替在用户真实 ChatGPT 页面中的验证。当前运行环境禁止访问 ChatGPT 域名，尚未成功用用户真实分享页完成端到端验证；不会把只有页面标题的结果说成读完正文。

## 备用油猴脚本

如果已经使用 Tampermonkey，可以用同发布目录的 `chatgpt-share-guard.user.js`。没有 Tampermonkey 时优先按上面的本地扩展步骤安装。

若浏览器没有自动弹出 `.user.js` 安装窗口：油猴管理面板 → 添加新脚本 → 粘贴 `.user.js` 全部源码 → 保存。按 Tampermonkey 的 [官方说明](https://www.tampermonkey.net/faq.php?q=Q209) 开启“允许用户脚本”，或在没有此开关的 Chromium 版本开启扩展开发者模式。脚本尽早注入，但不能保证比网站全部监听器更早运行。

源码独立实现，分享页格式研究参考 MIT 项目 [chatgpt-share-reader](https://github.com/roykollensvendsen/chatgpt-share-reader)。
