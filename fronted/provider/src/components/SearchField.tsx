import { Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export function SearchField({ value, onValueChange, placeholder, ariaLabel, className }: {
  value: string
  onValueChange: (value: string) => void
  placeholder: string
  ariaLabel: string
  className?: string
}) {
  return <div className={cn("search-shell", className)}>
    <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    <Input data-search-input value={value} onChange={event => onValueChange(event.target.value)} maxLength={64} placeholder={placeholder} aria-label={ariaLabel} />
    {value && <Button type="button" variant="ghost" size="icon" className="-mr-2 size-11 shrink-0" onClick={() => onValueChange("")} aria-label={`清空${ariaLabel}内容`}><X className="size-4" /></Button>}
  </div>
}
