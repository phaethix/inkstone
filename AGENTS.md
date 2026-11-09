# AGENTS.md

- 规则1：写完代码后必须执行uvx ruff format . 格式化代码

## 生图服务注意事项

- 规则2：Agnes i2i 队列的 `negative_prompt` 会被服务端静默丢弃（详见 docs/architecture/2026-11-09-image-provider-constraints.md）。禁止依赖负面提示词传递约束；必须用正向措辞在 prompt 里描述想要的画面（如 "edge-to-edge artwork"），而不是禁止不想要的元素。prompt 中的引文、dialogue、chrome 类词汇会被 Qwen 文本编码器读懂并画进画面，图像 prompt 不得包含审计引文与台词字样。
