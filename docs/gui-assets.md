# GUI 图标与视觉资产清单

本项目 GUI 不打包第三方图标集、图片字体或商业视觉素材。

- 工作流画布节点、连线、状态标签和插入按钮由 Qt `QPainter` 运行时绘制。
- Activity Bar 与 Bottom Panel 状态入口使用项目自制单色 SVG，源文件位于
  `src/gui/assets/icons/`，由 `src/gui/resources.qrc` 编译进入 Qt Resource，运行时
  只通过 `:/icons/...` 读取，不依赖当前工作目录。
- `src/gui/resources_rc.py` 是由 PySide6 `pyside6-rcc` 从 `.qrc` 生成的资源模块，
  不得手工修改；更新 SVG 或 `.qrc` 后必须重新生成并执行 wheel smoke。
- 图标加载器按当前 Qt Palette 重着色，并预渲染 1x/2x/3x 尺寸；Activity Bar
  使用主题前景色，强调色 Status Bar 使用白色前景。
- 中性颜色来自系统 `QPalette`；动作类别色和运行状态色是项目内定义的设计令牌。
- 主要命令按钮使用简短文字和语义色，不再混用 Emoji 装饰符号。
- 后续增加 SVG、PNG、字体或第三方图标库时，必须在本文件记录来源、版本、
  许可证、修改情况和打包路径，再允许进入发布包。

当前 8 个 SVG 为本项目原创资产，不含第三方图形，使用 CC0 1.0；完整声明位于
`src/gui/assets/icons/LICENSE.txt`。wheel 必须包含 SVG 源文件、许可证、`.qrc`
和编译后的资源模块。
