# 普通聊天浏览器版：验证记录

日期：2026-10-18。测试浏览器为 Chromium 154.0.8037.57，Debian GNU/Linux 13。

## 已通过的自动测试

**43 项核心 JavaScript 测试通过**：URL、重复 JSON 字段、React Router 分片引用、旧版数据、当前分支顺序、长链、缺父节点、环、未完成可见消息、隐藏消息过滤、未知类型、媒体附件、大小深度限制，以及提示词中原文封装和预算等。

```text
node --test browser/core.test.js
```

**25 项浏览器流程测试通过**：在 Chromium 中用本次的原始 content/core 脚本于 document_start 注入合成 ChatGPT 页面，所有 HTTP 响应均被测试路由模拟，没有真实账号或真实网站请求。

```text
python3 browser/browser_test.py --extension-dir browser
```

包含 textarea、contenteditable、10 月无旧 ID 输入框、Lexical、中文发送按钮、独立表单、Enter/click/submit、中文输入法、Shift+Enter、普通消息不干预、重复手势单次事务、读取时改字/换聊天取消、网络/结构/附件/超长失败、损坏链接阻止、非 blob 媒体、第二次事务与未保护状态等。

实际 native execCommand 测试发现可选空行会使 contenteditable 的 innerText 多出换行，现已修复提示词包装；原文 JSON 内的换行不变。测试模拟网页从 input 事件同步草稿再发送，因此不把“只改了 DOM 而未更新输入状态”当作成功。

## 实际扩展加载测试：被环境策略阻止

尝试了 Chromium persistent context 的真实 unpacked extension 加载，并移除 Playwright 默认禁用扩展参数。浏览器日志明确返回：

```text
Loading of unpacked extensions is disabled by the administrator.
```

扩展注册列表没有本扩展。当前云端浏览器存在管理员强制策略；没有修改或绕过这项策略。

因此 25 项通过的是**真实浏览器中的页面脚本行为测试**，不是成功安装扩展的证明。没有把脚本注入测试冒充扩展安装测试。Manifest 的版本、脚本路径与单一匹配域名已静态检查；仍需在用户自行管理的 Chrome/Edge 中完成安装验证。

## 真实分享页：未成功验证

用户给过一个真实公开分享链接。本运行环境的网络允许列表不包含 ChatGPT，正常请求受到代理 403 限制；浏览工具只返回标题。没有获取可用于端到端验证的真实正文，也没有测试登录用户的实际 ChatGPT 编辑器和模型回复。

安装扩展后，分享页请求来自用户自己的浏览器，会按同源规则使用当前 ChatGPT 会话。测试环境的网络策略与用户浏览器是否能打开分享页是两件事；不能以本环境的 403 推断链接已失效。

## 能力边界

- 自动操作只对已识别的输入框及发送入口生效。未识别时必须显示“未保护”。
- 不伪装成官方扩展、技能功能或稳定的 ChatGPT API。网页 DOM 和分享数据格式都可能改变。
- 自动发送仅触发一次，不通过输入框清空来声称服务端已接收；需要看聊天界面确认。
- 读取失败和预算超限不截断源数据、不继续按裸链接猜答；未知发送入口不承诺全部拦截。
- 原文进入提示词、条数、首尾核对和编号引用都不能保证模型实际完整阅读、正确推理或零幻觉。
