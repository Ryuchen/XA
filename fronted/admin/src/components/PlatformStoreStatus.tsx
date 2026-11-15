import { Status } from "@/components/business"

const storeLabels: Record<string, string> = {
  ACTIVE: "营业中",
  DISABLED: "已停用",
  PROVISIONING: "待初始化",
  CONFIGURING: "待配置",
}

export function PlatformStoreStatus({ value }: { value?: string }) {
  return <Status value={value} label={value ? storeLabels[value] || "状态待核对" : undefined}
    tone={value === "PROVISIONING" || value === "CONFIGURING" ? "warning" : undefined} />
}

export function PlatformReadStatus({ value }: { value?: string }) {
  const labels: Record<string, string> = {
    OK: "已读取", NOT_ACTIVE: "未营业或未初始化", NOT_INITIALIZED: "未初始化", UNAVAILABLE: "暂不可用",
  }
  return <Status value={value} label={value ? labels[value] || "状态待核对" : undefined}
    tone={value === "OK" ? "success" : value === "UNAVAILABLE" ? "destructive" : "muted"} />
}
