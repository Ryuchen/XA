import type { ReactNode } from "react"
import { PageHeader } from "@/components/business"
import { CircleAlert } from "@/components/ui/icons"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export function RecordHeading(props: {
  title: string
  description?: string
  children?: ReactNode
  tone?: "plain" | "feature"
  tag?: string
  metrics?: Array<{ label: string; value: ReactNode }>
  metricsColumns?: number
}) {
  return <PageHeader {...props} tone="feature" />
}

export function RecordSection({
  title,
  children,
  note,
  className = "",
}: {
  title: string
  children: ReactNode
  note?: string
  className?: string
}) {
  return (
    <Card className={`admin-record-section ${className}`}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {note && <CardDescription>{note}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function RecordNotice({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="admin-record-notice">
      <CircleAlert size={16} aria-hidden="true" />
      <div>
        <h2>{title}</h2>
        <p>{children}</p>
      </div>
    </section>
  )
}
