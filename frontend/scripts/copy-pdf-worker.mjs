// 把与 react-pdf 匹配版本的 pdf.js worker、CMap（中日韩字体必需）和标准字体复制到 public/
import { copyFileSync, cpSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const reactPdfDir = path.dirname(require.resolve("react-pdf/package.json"));
const pdfjsDir = path.dirname(require.resolve("pdfjs-dist/package.json", { paths: [reactPdfDir] }));
mkdirSync("public", { recursive: true });
copyFileSync(path.join(pdfjsDir, "build", "pdf.worker.min.mjs"), path.join("public", "pdf.worker.min.mjs"));
cpSync(path.join(pdfjsDir, "cmaps"), path.join("public", "cmaps"), { recursive: true });
cpSync(path.join(pdfjsDir, "standard_fonts"), path.join("public", "standard_fonts"), { recursive: true });
console.log("pdf.js assets copied from", pdfjsDir);
