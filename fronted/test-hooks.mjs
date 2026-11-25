import { existsSync } from "node:fs"
import { readFile } from "node:fs/promises"
import { fileURLToPath, pathToFileURL } from "node:url"
import ts from "typescript"

const extensions = [".ts", ".tsx"]

export async function resolve(specifier, context, nextResolve) {
  if (/^\.{1,2}\//.test(specifier) && !/\.[a-z0-9]+$/i.test(specifier) && context.parentURL) {
    const base = fileURLToPath(new URL(specifier, context.parentURL))
    for (const suffix of [...extensions, "/index.ts", "/index.tsx"]) {
      const candidate = `${base}${suffix}`
      if (existsSync(candidate)) return nextResolve(pathToFileURL(candidate).href, context)
    }
  }
  return nextResolve(specifier, context)
}

export async function load(url, context, nextLoad) {
  if (url.endsWith(".tsx")) {
    const source = await readFile(fileURLToPath(url), "utf8")
    return {
      format: "module",
      shortCircuit: true,
      source: ts.transpileModule(source, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        fileName: fileURLToPath(url),
      }).outputText,
    }
  }
  if (/\.(?:avif|gif|jpe?g|png|svg|webp)$/i.test(url)) {
    return { format: "module", shortCircuit: true, source: `export default ${JSON.stringify(fileURLToPath(url))}` }
  }
  if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" }
  return nextLoad(url, context)
}
