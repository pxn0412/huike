# 第三方组件与参考

- PDF.js / pdfjs-dist 5.4.624：Mozilla，Apache-2.0。用于 PDF 页渲染。安装包许可证位于 `node_modules/pdfjs-dist/LICENSE`，相关标准字体和解码组件声明在包内相应目录。分发时保留许可文件。
- pypdf 6.16.1：BSD-3-Clause。用于 PDF 文本提取与测试夹具。
- Playwright 1.58.2：Apache-2.0。仅用于浏览器测试。
- Windows Media OCR：调用本机系统能力，不重新分发系统组件。
- Poppler：使用本机现有 pdftoppm 程序，不将该二进制复制到本项目。

网站页面为本项目编写，没有复制 Youtu-RAG 页面源码。Youtu-RAG、Docling、assistant-ui 等仍是调研候选，不属于已集成依赖。
