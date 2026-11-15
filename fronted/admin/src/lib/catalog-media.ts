import { getSession } from "@xa/shared"
import { adminClient } from "@/api/request"

export function catalogMediaSource(source: string): string {
  try {
    const target = new URL(source, window.location.origin)
    return target.origin === window.location.origin && target.pathname.startsWith("/media/")
      ? `${target.pathname}${target.search}`
      : ""
  } catch {
    return ""
  }
}

/** Never send tenant credentials to a third-party image host. */
export async function readCatalogMedia(source: string): Promise<string> {
  const path = catalogMediaSource(source)
  if (!path) throw new Error("图片地址无效")
  for (let retry = 0; retry < 2; retry += 1) {
    const token = getSession("admin").token
    if (!token) throw new Error("请先登录后查看图片")
    const response = await fetch(path, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "same-origin",
      cache: "no-store",
    })
    if (response.status === 401 && retry === 0) {
      await adminClient.get("/admin/auth/profile")
      continue
    }
    if (!response.ok) throw new Error("图片暂不可读取")
    const blob = await response.blob()
    if (!blob.type.startsWith("image/")) throw new Error("图片格式不可用")
    return URL.createObjectURL(blob)
  }
  throw new Error("图片暂不可读取")
}
