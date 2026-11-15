export interface AdminOperationConfig {
  pointsEnabled: boolean
  birthdayMultiplier: number
  vipEnabled: boolean
  seasonCode: string
  seasonEndsAt: string
  updatedAt: string
}

export const initialAdminOperationConfig: AdminOperationConfig = {
  pointsEnabled: true,
  birthdayMultiplier: 2,
  vipEnabled: true,
  seasonCode: 'S8',
  seasonEndsAt: '2026-12-31T23:59:00',
  updatedAt: '2026-11-04T11:30:00',
}

export function formatAdminOperationTime(value: string) {
  return value.replace('T', ' ').slice(0, 16)
}
