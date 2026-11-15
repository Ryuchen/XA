import { useId, type ReactNode } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { Row } from "@/components/business"
import { Status } from "@/components/Status"

export function PayrollInput({
  label,
  value,
  onChange,
  type = "text",
  hint,
  required,
  disabled,
  money,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  hint?: string
  required?: boolean
  disabled?: boolean
  money?: boolean
}) {
  const id = useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        name={label}
        autoComplete="off"
        type={type}
        value={value}
        inputMode={money ? "decimal" : undefined}
        required={required}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      {hint && (
        <p
          id={`${id}-hint`}
          className="text-xs leading-5 text-muted-foreground"
        >
          {hint}
        </p>
      )}
    </div>
  )
}

export function PayrollSelect({
  label,
  value,
  onChange,
  options,
  disabled,
  required,
  placeholder = "请选择",
}: {
  label: string
  value: unknown
  onChange: (value: string) => void
  options: Array<[string | number, string]> | Row[]
  disabled?: boolean
  required?: boolean
  placeholder?: string
}) {
  const id = useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        name={label}
        autoComplete="off"
        className="h-9 w-full rounded-md border border-input bg-input/30 px-3 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-input/50"
        value={String(value ?? "")}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        required={required}
      >
        <option value="">{placeholder}</option>
        {options.map((option) => {
          const [key, name] = Array.isArray(option)
            ? option
            : [option.id, option.name || option.account_name || option.label]
          return (
            <option key={key} value={key}>
              {name}
            </option>
          )
        })}
      </select>
    </div>
  )
}

export function PayrollNotice({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      className="rounded-lg border bg-muted/40 px-4 py-3 text-sm leading-6"
    >
      {children}
    </div>
  )
}

export function PayrollWarnings({ warnings = [] }: { warnings?: string[] }) {
  if (!warnings.length) return null
  return (
    <PayrollNotice>
      <ul className="list-disc space-y-1 pl-4">
        {warnings.map((warning, index) => (
          <li key={`${index}-${warning}`}>{warning}</li>
        ))}
      </ul>
    </PayrollNotice>
  )
}

export function PendingResult({ children }: { children: ReactNode }) {
  return <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
    <Status label="结果待核对" tone="warning" />
    <span>{children}</span>
  </div>
}
