import { useEffect, useId, useMemo, useState } from "react"
import { Check, ChevronDown, Search, X } from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { Row } from "@/components/business"

type Pagination = {
  page: number
  total: number
  loading?: boolean
  onPrevious: () => void
  onNext: () => void
}

function optionText(row: Row, keyField: string) {
  return `${row.nickname || row.display_name || row.name || row.title || row.username || row.service_name || `选项 #${row[keyField]}`} ${row.account_no || row.escort_no || row.boss_no || `#${row[keyField]}`}${row.username ? ` · ${row.username}` : ""}`
}

export default function SearchableSelect({
  label,
  choices,
  keyField,
  value,
  onChange,
  multiple = false,
  disabled = false,
  loading = false,
  searchSupported = true,
  onSearch,
  allLabel = "全部",
  pagination,
}: {
  label: string
  choices: Row[]
  keyField: string
  value: any
  onChange: (value: any, selected?: Row) => void
  multiple?: boolean
  disabled?: boolean
  loading?: boolean
  searchSupported?: boolean
  onSearch?: (keyword: string) => void
  allLabel?: string
  pagination?: Pagination
}) {
  const [open, setOpen] = useState(false)
  const controlId = useId()
  const [search, setSearch] = useState("")
  const selected = useMemo(() => (Array.isArray(value) ? value : value ? [value] : []).map(Number), [value])
  const filtered = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase()
    if (!keyword) return choices
    return choices.filter(row => optionText(row, keyField).toLocaleLowerCase().includes(keyword))
  }, [choices, keyField, search])
  useEffect(() => {
    if (open) setSearch("")
  }, [open])
  useEffect(() => {
    if (!searchSupported) return
    const timer = window.setTimeout(() => onSearch?.(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search, onSearch, searchSupported])
  const selectedChoice = !multiple && value != null && value !== ""
    ? choices.find(row => Number(row[keyField]) === Number(value))
    : null
  const triggerText = multiple
    ? selected.length ? `已选 ${selected.length} 项` : "请选择"
    : selectedChoice ? optionText(selectedChoice, keyField) : allLabel

  function selectRow(row: Row) {
    const id = Number(row[keyField])
    if (multiple) {
      const next = selected.includes(id) ? selected.filter(item => item !== id) : [...selected, id]
      onChange(next, row)
    } else {
      onChange(id, row)
      setOpen(false)
    }
  }

  return (
    <div className="space-y-2" data-searchable-select={multiple ? "multiple" : "single"}>
      <Label htmlFor={controlId}>{label}</Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id={controlId}
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label={label}
            disabled={disabled || loading}
            className="h-10 w-full min-w-0 justify-between gap-2 rounded-lg px-3 font-normal"
          >
            <span className="min-w-0 flex-1 truncate text-left">{triggerText}</span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] gap-0 overflow-hidden p-0">
          {searchSupported && (
            <div className="relative border-b p-2">
              <Search className="pointer-events-none absolute top-1/2 left-5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label={`搜索${label}选项`}
                name={`search-${label}`}
                autoComplete="off"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder="输入名称、账号或编号"
                className="border-0 bg-transparent pl-8 shadow-none focus-visible:ring-0"
                disabled={loading}
              />
            </div>
          )}
          {multiple && (
            <div className="flex items-center justify-between gap-2 border-b px-2 py-1 text-xs">
              <Button type="button" variant="ghost" size="sm" disabled={!filtered.length || loading} onClick={() => onChange(filtered.map(row => Number(row[keyField])))}>选择当前结果</Button>
              <Button type="button" variant="ghost" size="sm" disabled={!selected.length} onClick={() => onChange([])}><X className="size-3.5" />清除</Button>
            </div>
          )}
          <div className="max-h-72 overflow-y-auto p-1" role="listbox" aria-label={label} aria-multiselectable={multiple || undefined}>
            {!multiple && (
              <button
                type="button"
                role="option"
                aria-selected={value == null || value === ""}
                className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent"
                onClick={() => { onChange(null); setOpen(false) }}
              >
                <span className="grid size-4 shrink-0 place-items-center">{(value == null || value === "") && <Check className="size-4 text-primary" />}</span>
                <span>{allLabel}</span>
              </button>
            )}
            {filtered.map(row => {
              const id = Number(row[keyField])
              const checked = multiple ? selected.includes(id) : Number(value) === id
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={checked}
                  key={id}
                  className="flex min-h-10 w-full items-start gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent"
                  onClick={() => selectRow(row)}
                >
                  <span className="mt-0.5 grid size-4 shrink-0 place-items-center">{checked && <Check className="size-4 text-primary" />}</span>
                  <span className="min-w-0 flex-1 break-words">{optionText(row, keyField)}</span>
                </button>
              )
            })}
            {!filtered.length && !loading && <p className="px-3 py-4 text-center text-sm text-muted-foreground">没有匹配的选项</p>}
            {loading && <p className="px-3 py-4 text-center text-sm text-muted-foreground">正在读取选项…</p>}
          </div>
          {pagination && (
            <div className="flex items-center justify-between gap-2 border-t px-2 py-1 text-xs text-muted-foreground">
              <Button type="button" variant="outline" size="sm" disabled={pagination.loading || pagination.page <= 1} onClick={pagination.onPrevious}>上一页</Button>
              <span>第 {pagination.page} 页 · 共 {pagination.total} 项</span>
              <Button type="button" variant="outline" size="sm" disabled={pagination.loading || pagination.page * 50 >= pagination.total} onClick={pagination.onNext}>下一页</Button>
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  )
}
