import { existsSync } from "node:fs"
import { fileURLToPath, pathToFileURL } from "node:url"

export async function resolve(specifier, context, nextResolve) {
  if (!specifier.startsWith("@/")) return nextResolve(specifier, context)
  const base = fileURLToPath(new URL(`../src/${specifier.slice(2)}`, import.meta.url))
  for (const suffix of ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]) {
    const candidate = base + suffix
    if (existsSync(candidate)) return nextResolve(pathToFileURL(candidate).href, context)
  }
  return nextResolve(specifier, context)
}
