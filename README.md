# Personal Memory Network

[中文](#个人记忆网络) · [License](LICENSE)

Chats with ChatGPT, Claude, Gemini, and Grok stay scattered, and every new conversation starts from nothing. This app keeps a personal knowledge base on your own computer. You choose which chats and notes go in. A model distills them down to your decisions, your progress, and the approaches you already rejected. You then paste that context into any AI, so it does not start from zero.

Models only compute. The memory stays in plain Markdown on your machine.

![Four panels: starting from zero, dropping notes in, keeping decisions and dead ends, handing the result to any AI](docs/comic.jpg)

1. Every chat starts from zero.
2. Drop the chats and notes in.
3. Keep only your decisions and dead ends.
4. Hand that to any AI.

## Features

- **You choose the source.** Put official exports or your own notes in one folder. Nothing else on the disk is read.
- **It merges instead of piling up.** A newer fact replaces the old one. Abandoned ideas are marked so they are not recommended again. Generic explanations written by the assistant are dropped.
- **The base stays small.** A short profile, one volume per topic, and detail cards that are attached only when a question needs them. A volume that grows too long is compressed or split, not cut off.
- **Mount it anywhere.** Copy one pack into ChatGPT, Claude, Gemini, Grok, or Cursor.
- **Readable files.** The result is Markdown. Open it in any editor and change it by hand. Each distillation run keeps a snapshot.

## Quick start

Python 3.10 or newer.

```bash
git clone https://github.com/qqqqxun/personal-memory-network.git
cd personal-memory-network
python -m pip install -r requirements.txt
python -m app
```

On Windows you can double-click `start.bat`. Open the local address printed in the terminal.

1. On the settings page, add an API key for the model you want to use. Gemini is the default. A local model through Ollama also works.
2. Put chat exports or notes in the inbox folder shown in the app, or upload them there.
3. Run one distillation pass. Then open the mount page, copy the pack, and paste it into any AI.

Supported exports: ChatGPT, Claude, Gemini (Google Takeout), and Grok. Markdown, text, and source files work too.

## Privacy

The app does not upload your memory anywhere by itself. Distillation sends the material you placed in the inbox to the model provider you configured. Do not put anything there that you would not send to that provider. Secrets such as keys and card numbers are redacted locally before that call. The knowledge base itself is unencrypted Markdown, so keep that folder to yourself.

## Development

```bash
python -m pytest -q
```

| Path | Role |
|---|---|
| `app/vault.py` | Library, volumes, cards, snapshots, mount pack |
| `app/distiller.py` | Distillation, splitting long volumes, model calls |
| `app/importers/` | Chat export parsers |
| `app/main.py` | Local service |
| `app/static/` | Web interface |
| `extension/` | Optional browser extension |

## Contributing

Issues and pull requests are welcome. Please do not include personal chats, API keys, or local configuration in a report.

## License

[MIT](LICENSE). You can use, modify, and redistribute this project, including commercially, as long as you keep the copyright notice. It is provided without warranty.

---

# 个人记忆网络

和 ChatGPT、Claude、Gemini、Grok 的聊天散落各处，每次新对话都从零开始。这个程序在你自己的电脑上维护一份个人知识库：你决定放进哪些聊天和笔记，模型把它们蒸馏成你的决定、进度和已经放弃的方案，你再把这段背景粘给任何一个 AI。

模型只负责计算。记忆是你电脑上的普通 Markdown 文件。

上图四格的意思是：

1. 每次都从零开始。
2. 把聊天和笔记丢进去。
3. 只留下你的决定和踩坑。
4. 换任何一个 AI，都能接上。

## 能做什么

- **读什么由你决定。** 只处理你放进投喂文件夹的导出文件和笔记，不扫描电脑上的其他内容。
- **合并，而不是越堆越多。** 新事实替换旧事实。放弃过的做法会标明，避免再次被推荐。模型回答里的通用套话会被丢掉。
- **知识库保持短。** 一份简短的个人画像、每个主题一卷专卷，细节另存为卡片，只在相关问题时带上。一卷写不下时会压缩或拆开，不会直接截断。
- **挂到任何 AI。** 复制一段挂载包，粘进 ChatGPT、Claude、Gemini、Grok 或 Cursor。
- **文件可以直接打开。** 结果是 Markdown，可以用任何编辑器修改。每一轮蒸馏都会留下一份快照。

## 开始使用

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/qqqqxun/personal-memory-network.git
cd personal-memory-network
python -m pip install -r requirements.txt
python -m app
```

Windows 下也可以双击 `start.bat`。然后打开终端里打印的本机地址。

1. 在「投喂与算力配置」填入要使用的模型 API Key。默认是 Gemini，也可以改用本机的 Ollama。
2. 把聊天导出或笔记放进页面上显示的投喂文件夹，或直接在页面上传。
3. 跑一轮蒸馏，再到「一秒挂载」复制背景，粘给任何一个 AI。

可以导入 ChatGPT、Claude、Gemini（Google Takeout）和 Grok 的官方导出，也可以直接放入 Markdown、文本和代码文件。

## 隐私

程序不会自己把记忆上传到别处。蒸馏时，你放进投喂区的内容会发给你配置的那个模型服务商；不要放入不愿发给它的内容。密钥、卡号等会在发出前于本地打成占位符。知识库是未加密的 Markdown，请自己保管那个文件夹。

## 开发

```bash
python -m pytest -q
```

| 路径 | 作用 |
|---|---|
| `app/vault.py` | 记忆库、专卷、卡片、快照和挂载包 |
| `app/distiller.py` | 蒸馏、超长拆卷、模型调用 |
| `app/importers/` | 各家聊天导出的解析 |
| `app/main.py` | 本机服务 |
| `app/static/` | 网页界面 |
| `extension/` | 可选的浏览器扩展 |

## 参与贡献

欢迎提 Issue 和 Pull Request。请不要在反馈里附上个人聊天记录、API Key 或本机配置。

## 许可证

[MIT](LICENSE)。可以自由使用、修改和再分发，包括商业用途，只需保留版权声明。软件按原样提供，不含任何担保。
