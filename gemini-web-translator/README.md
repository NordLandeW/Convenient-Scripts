# Webpage Translator

使用 Gemini、OpenRouter 或 OpenAI / 兼容 API 翻译网页的 Tampermonkey 脚本。支持 Chat Completions / Responses、流式回填、并发批次和原文还原。

## 安装与配置

1. 将 `gemini-web-translator.js` 安装到 Tampermonkey。
2. 从 Tampermonkey 菜单选择对应平台的“密钥”，在浏览器原生对话框中输入 API Key。取消保持原值；留空后确认可删除该平台密钥。
3. 使用 OpenAI 兼容服务时，从菜单“OpenAI / 兼容 API 地址”设置 Base URL，并核对确认框中的完整目的地址。可输入根地址、API 前缀或以 `/chat/completions`、`/responses` 结尾的地址，例如 `https://gateway.example/v1`。密钥和网页内容将发送到这个服务；HTTP 地址不提供传输加密。
4. 按 **Alt+S** 或选择菜单“设置”，选择平台、模型、目标语言和其它参数。手动填写的模型代码优先于下拉列表；推理强度默认不指定，沿用服务端行为。
5. 按 **Alt+T** 开始翻译；正在翻译时再次按下会取消并还原，已有译文时再次按下会还原。

脚本请求 `@sandbox DOM`，敏感操作检查实际 `GM_info.sandboxMode`，接受 `dom` 和 `js`。若脚本管理器报告 `raw` 或未知模式，脚本会提示并停止密钥管理和翻译；需要使用支持隔离沙箱的 Tampermonkey 环境。其它脚本管理器的兼容性取决于这些 API 和隔离语义的实现。

已有平台密钥继续使用原来的 GM 存储键。网页设置面板仅显示是否已配置，不读取密钥到输入框，也不能修改 API 地址。浏览器原生对话框由油猴菜单打开；不要把密钥填入网页仿制的表单。一般设置仍位于网页共享 DOM，并不是隔离的扩展设置页。

## 开发验证

在仓库根目录执行，无需安装 Node 单元测试框架：

```sh
node --check gemini-web-translator/gemini-web-translator.js
node --test gemini-web-translator/tests/*.test.cjs
```

测试使用真实源码函数、隔离 GM 存储和假 API，不读取真实密钥或向翻译服务发送请求。
