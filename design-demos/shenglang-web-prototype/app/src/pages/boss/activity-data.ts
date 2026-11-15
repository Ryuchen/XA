export interface ClubActivity {
  id: number
  title: string
  date: string
  image: string
  archived: boolean
}

export const clubActivities: ClubActivity[] = [
  { id: 201, title: '双排默契计划', date: '2026-11-03', image: './activity-duo.svg', archived: false },
  { id: 202, title: '声浪周末挑战赛', date: '2026-10-30', image: './activity-weekend.svg', archived: true },
  { id: 203, title: '新老板见面礼', date: '2026-10-18', image: './activity-welcome.svg', archived: true },
]
