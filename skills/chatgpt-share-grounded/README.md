# ChatGPT 分享原文核对

把这个技能装在**接收分享链接的账号/客户端**。安装后，在新聊天直接发送 `https://chatgpt.com/share/…`，或在链接后加问题，技能会尝试自动提取分享页原文，再依据原文回答。

它减少的是“原文没读到却继续编写”的情况。不能保证模型零幻觉、每次自动触发，也不能改变账号的联网和代码执行权限。

## ChatGPT：上传安装

适用于账号已经开放 Skills 且允许上传、运行代码的情况。

1. 下载 `chatgpt-share-grounded.zip`，保留 ZIP。
2. 在 ChatGPT 侧栏打开 **Plugins（插件）→ Skills（技能）→ Create（创建）→ Upload from your computer（从电脑上传）**。
3. 选择 ZIP，完成界面显示的扫描和安装步骤。
4. 在安装该技能的接收账号，新建聊天并发送一个公开分享链接。

界面名称可能随语言或版本变化。官方说明：[Skills in ChatGPT](https://help.openai.com/en/articles/20001066-skills-in-chatgpt)。技能可用性取决于账号和工作区配置。

## Codex：安装本地技能

将 ZIP 上传/附加给 Codex，发送：

> 请检查这个本地技能包，将其中的 chatgpt-share-grounded 文件夹安装到当前客户端使用的技能目录，启用隐式调用。不要把安装包发布到外部。安装后确认 SKILL.md 和 scripts/read_share.py 可用。

或解压后，将 `chatgpt-share-grounded` 文件夹放入你当前 Codex 配置的技能目录。在会话中确认客户端已发现该技能；必要时重新打开会话。

如需显式调用一次：

```text
使用 $chatgpt-share-grounded 读取 https://chatgpt.com/share/你的分享ID
```

## 使用效果

以后只发：

```text
https://chatgpt.com/share/你的分享ID
```

或：

```text
https://chatgpt.com/share/你的分享ID
我们最后确定的三个要求是什么？请给出对应原句。
```

核对成功后，应看到“本次分享快照 N 条文字”、首尾短原句、摘要或带 `[T0001]` 编号的回答。看到这些回执可以帮助核对，但它们本身不能证明模型一定没有遗漏或编造。

如果客户端没有自动启用，使用上面的显式调用语句；安装包不能强制客户端路由。

## 什么情况下会停止

- 当前环境不能访问 `chatgpt.com`，或没有 Python/脚本工具。
- 403、登录/验证页、已删除链接或页面数据格式发生变化。
- 当前分支的节点链缺失、出现环、没有可见文字或未知可见内容。
- 图片、附件、音视频等不能完整读取。
- 正文过长，无法在当前上下文中实际读完。默认超过 12000 个正文字符时先报告“已提取，尚未读全”，不偷偷截断。

“完整”只表示**当前分享页中支持的可见文字分支**，不包括原账号未分享的历史、未更新的内容、附件或隐藏信息。

## 隐私和运行环境

只需要 Python 3.10+ 标准库，无需 pip、Node、API 密钥或第三方服务器。脚本只对用户给出的合法公开分享地址发起 HTTPS 请求，不读取 Cookie、不遥测、不上传到代理服务。保留运行环境提供的正常网络代理和 TLS 校验。

提取的原文保存在客户端工作目录中，随后由模型通过工具读取。按客户端本身的数据处理规则使用；它不是“聊天内容永不离开设备”的承诺。

## 验证记录

提供解析器、网络失败、文件完整性、分块阅读和命令行集成测试。详细结果见随包 `VALIDATION.md`。

**已尝试用户给出的真实分享链接，但本次执行环境的网络策略阻止访问，尚未成功完成真实链接正文提取或跨账号安装验证。** 浏览工具只取得页面标题，不能作为已读取正文的证据。合成样本通过不能保证 ChatGPT 每次改版后都可用；脚本会保留明确错误，不按链接猜答。离线 HTML 测试会标为 `local_html`，不能冒充在线读取。

## 文件

- `SKILL.md`：自动触发、读取覆盖检查和回答规则。
- `scripts/read_share.py`：抓取并导出每条可见文字。
- `scripts/share_parser.py`：保守解析分享页数据。
- `scripts/read_chunk.py`：校验文件并分页返回正文，不靠大段一次性输出。
- `agents/openai.yaml`：兼容客户端的界面信息与隐式调用设置。

## 来源

分享页格式研究参考 [chatgpt-share-reader](https://github.com/roykollensvendsen/chatgpt-share-reader)，本包独立实现严格检查。另发现的 [chat-share-reader MCP](https://github.com/pencil311/chat-share-reader) 可以作为支持开发者模式客户端的远程接入方案；本包不依赖也不自动连接该服务。
