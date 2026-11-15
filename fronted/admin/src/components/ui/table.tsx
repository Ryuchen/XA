"use client"

import * as React from "react"
import { cn } from "cn"

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      role="region"
      aria-label="数据表，可左右滚动查看更多列"
      tabIndex={0}
      className="relative w-full overflow-x-auto"
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("bg-muted/40 [&_tr]:border-b", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-border/70 transition-colors hover:bg-muted/40 focus-within:bg-muted/40 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-11 px-4 text-left align-middle text-xs font-medium whitespace-nowrap text-muted-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-4 py-3 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

export type TableRowKey = string | number
export interface TableSelectionControl {
  selectedKeys?: TableRowKey[]
  onChange?: (keys: TableRowKey[]) => void
}

export function useTableSelection(keys: TableRowKey[], control: TableSelectionControl = {}) {
  const [internalKeys, setInternalKeys] = React.useState<TableRowKey[]>([])
  const selectedKeys = control.selectedKeys ?? internalKeys
  const keySignature = keys.map(String).join("|")
  React.useEffect(() => {
    if (control.selectedKeys === undefined) {
      setInternalKeys(current => current.filter(key => keys.includes(key)))
    }
  }, [keySignature])
  const update = control.onChange ?? setInternalKeys
  const allSelected = keys.length > 0 && keys.every(key => selectedKeys.includes(key))
  const someSelected = !allSelected && keys.some(key => selectedKeys.includes(key))
  return {
    selectedKeys,
    hasRows: keys.length > 0,
    allSelected,
    someSelected,
    clear: () => update([]),
    toggle: (key: TableRowKey) => update(selectedKeys.includes(key) ? selectedKeys.filter(item => item !== key) : [...selectedKeys, key]),
    toggleAll: () => update(allSelected ? [] : [...keys]),
  }
}
export type TableSelectionState = ReturnType<typeof useTableSelection>

function TableSelectionHead({ selection }: { selection: TableSelectionState }) {
  const ref = React.useRef<HTMLInputElement>(null)
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = selection.someSelected
  }, [selection.someSelected])
  return <TableHead className="w-12 px-3 text-center">
    <input
      ref={ref}
      type="checkbox"
      className="size-4 align-middle accent-primary"
      aria-label="选择本页全部记录"
      aria-checked={selection.someSelected ? "mixed" : selection.allSelected}
      checked={selection.allSelected}
      disabled={!selection.hasRows}
      onChange={selection.toggleAll}
    />
  </TableHead>
}

function TableSelectionCell({ selection, rowKey, index, disabled = false }: { selection: TableSelectionState; rowKey: TableRowKey; index: number; disabled?: boolean }) {
  return <TableCell className="w-12 px-3 text-center">
    <input
      type="checkbox"
      className="size-4 align-middle accent-primary"
      aria-label={`选择第 ${index + 1} 行记录`}
      checked={selection.selectedKeys.includes(rowKey)}
      disabled={disabled}
      onClick={event => event.stopPropagation()}
      onChange={() => selection.toggle(rowKey)}
    />
  </TableCell>
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
  TableSelectionHead,
  TableSelectionCell,
}
