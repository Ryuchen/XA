/** Keep browser chrome aligned with the actual application surface. */
export function syncThemeColor() {
  const frame = requestAnimationFrame(() => {
    const color = getComputedStyle(document.body).backgroundColor
    if (!color || color === "transparent" || color === "rgba(0, 0, 0, 0)") return
    let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    if (!meta) {
      meta = document.createElement("meta")
      meta.name = "theme-color"
      document.head.appendChild(meta)
    }
    meta.content = color
  })
  return () => cancelAnimationFrame(frame)
}
