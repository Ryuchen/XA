import { useSession } from "@xa/shared"
export function useAdminAuth() {
  const session = useSession("admin")
  const profile: any = session.user
  const hasPerm = (permission?: string | string[]) =>
    !permission ||
    !!profile?.is_superuser ||
    (Array.isArray(permission) ? permission : [permission]).some((code) =>
      profile?.permissions?.includes(code)
    )
  return { ...session, profile, hasPerm }
}
