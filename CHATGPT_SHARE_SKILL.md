> **普通 ChatGPT 网页聊天请使用 [Chrome / Edge 浏览器版](CHATGPT_BROWSER.md)。** 本页的 Skills ZIP 和 Codex 命令只适用于具备相应技能入口的客户端，不能直接装进普通聊天。

# ChatGPT 分享原文核对：下载与安装

[下载技能安装包（ZIP）](https://github.com/tc06260000-bit/-/raw/refs/heads/main/dist/chatgpt-share-grounded.zip)

## 在支持 Skills 的 ChatGPT 安装

1. 下载上面的 ZIP。
2. 在接收分享链接的账号打开「插件 → 技能 → 创建 → 从电脑上传」。
3. 选择 ZIP，完成界面显示的扫描与安装步骤。
4. 新建聊天，直接发送公开的 chatgpt.com/share/… 链接。

## 在 Codex 安装

在 Codex 输入：

```text
$skill-installer 安装 https://github.com/tc06260000-bit/-/tree/main/skills/chatgpt-share-grounded
```

若没有自动触发，可显式输入：

```text
使用 $chatgpt-share-grounded 读取这个分享链接，核对原文后回答。
```

## 它做什么

- 自动提取当前分享快照中支持的可见文字分支。
- 校验父节点链、文件完整性、连续消息编号，并逐段读取。
- 回答关键结论使用 [T0001] 一类消息编号和短原句。
- 网络失败、内容不完整或无法支持的附件出现时明确停答。

运行环境需有 Python 3.10+、代码工具，并允许访问 chatgpt.com；不需要 API 密钥或第三方抓取服务器。默认超过 12000 个正文字符时先报告“已提取，尚未读全”，避免在上下文不足时声称全文已读。

**69 项测试通过，但不能保证零幻觉或每次自动技能路由。用户真实链接在本次执行环境因网络策略 403 未能完成正文提取；尚未完成用户账号中的安装及端到端验证。**

[技能源码与详细说明](skills/chatgpt-share-grounded/)
[测试记录与限制](skills/chatgpt-share-grounded/VALIDATION.md)
