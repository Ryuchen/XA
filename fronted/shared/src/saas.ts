export const SAAS_CAPABILITIES = {
  modules: "SAAS_MODULES_V1",
  orders: "PLATFORM_ORDERS_V1",
  dispatch: "PLATFORM_DISPATCH_V1",
  branding: "BOSS_H5_BRANDING_V1",
} as const;
export const SAAS_MODULES = [
  {
    code: "ADMIN",
    name: "后台管理端",
    sale: "固定模块",
    description: "客服派单、订单追踪、权限、售后与经营管理",
    customizable: false,
  },
  {
    code: "PROVIDER",
    name: "陪玩工作台",
    sale: "固定模块",
    description: "接单、履约、报单、收益与提现",
    customizable: false,
  },
  {
    code: "BOSS_H5",
    name: "老板端 H5",
    sale: "独立购买",
    description: "客户自主下单、订单追踪及专属品牌体验",
    customizable: true,
  },
] as const;
export type H5Branding = {
  display_name: string;
  primary_color: string;
  welcome: string;
  support_label: string;
  density: "comfortable" | "compact";
};
export const DEFAULT_H5_BRANDING: H5Branding = {
  display_name: "",
  primary_color: "#2563EB",
  welcome: "选择心仪陪玩，开启一局好时光",
  support_label: "联系客服",
  density: "comfortable",
};
export type H5Entitlement = {
  status: "NOT_PURCHASED" | "ACTIVE" | "SCHEDULED" | "EXPIRED" | "SUSPENDED";
  starts_at?: string;
  ends_at?: string;
  can_create_order: boolean;
  can_customize: boolean;
};
export type ModuleContract = {
  store_slug: string;
  version: string;
  read_at: string;
  h5: H5Entitlement;
  branding: H5Branding;
  allowed_actions: (
    "REQUEST_PURCHASE" | "REQUEST_CUSTOMIZATION" | "SAVE_BRANDING"
  )[];
  requests: {
    id: string;
    kind: "PURCHASE" | "CUSTOMIZATION";
    status: string;
    created_at: string;
    note: string;
  }[];
};
export const H5_ENTITLEMENT_LABELS: Record<H5Entitlement["status"], string> = {
  NOT_PURCHASED: "未购买",
  ACTIVE: "已开通",
  SCHEDULED: "待生效",
  EXPIRED: "已到期",
  SUSPENDED: "已暂停",
};
export function validateH5Branding(value: H5Branding): string {
  if (!value.display_name.trim() || value.display_name.trim().length > 30)
    return "品牌名称请输入 1–30 个字";
  if (!/^#[0-9a-f]{6}$/i.test(value.primary_color))
    return "请选择六位十六进制主题色";
  if (value.welcome.length > 60 || /[<>]/.test(value.welcome))
    return "欢迎语限 60 字纯文本";
  if (!value.support_label.trim() || value.support_label.length > 12)
    return "客服入口名称请输入 1–12 个字";
  if (!["comfortable", "compact"].includes(value.density))
    return "请选择展示密度";
  return "";
}
/** Explicit allowlist: customization can never submit fixed-module rules/CSS. */
export function h5BrandingPayload(value: H5Branding): H5Branding {
  const error = validateH5Branding(value);
  if (error) throw new Error(error);
  return {
    display_name: value.display_name.trim(),
    primary_color: value.primary_color.toUpperCase(),
    welcome: value.welcome.trim(),
    support_label: value.support_label.trim(),
    density: value.density,
  };
}
export function h5OrderingBlocked(entitlement?: H5Entitlement): boolean {
  return !!entitlement && entitlement.can_create_order !== true;
}
/** Readable white foreground for a tenant-selected brand color. */
export function h5Foreground(hex: string): "#FFFFFF" | "#020617" {
  const rgb = [1, 3, 5]
    .map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] > 0.179
    ? "#020617"
    : "#FFFFFF";
}
