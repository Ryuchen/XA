import { useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"
import { BookOpen } from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminFilters } from "@/components/AdminFilters"
import { DataPageState, Feedback, Field, FormSection, MutationDialog, Pagination, ReadOnlyNotice, Status, useList, type Row } from "@/components/business"
import { SourcePageHeader, SourceDirectoryCard, useSourceReviewState } from "@/pages/Catalog"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import type { DictionaryType } from "@/lib/quick-config"

export default function DictionaryItems({ type, title, description, catalogReady, reloadCatalog }: {
  type: DictionaryType; title: string; description: string; catalogReady: boolean; reloadCatalog: () => Promise<void>
}) {
  const auth = useAdminAuth()
  const [keyword, setKeyword] = useState("")
  const [filters, setFilters] = useState<Row>({ keyword: "", is_active: "" })
  const [editing, setEditing] = useState<Row | null>(null)
  const [removing, setRemoving] = useState<Row | null>(null)
  const list = useList("dictionary-items", { type, ...filters })
  const review = useSourceReviewState("/admin/dictionary-items/")
  const locked = !catalogReady || !list.data || list.loading || !!list.error || review.locked
  return <DataPageState loading={list.loading} hasSnapshot={!!list.data} error={list.error} retry={list.reload} skeleton="dictionary">
    <div className="admin-source-page space-y-4" data-resource="dictionary-items">
      <SourcePageHeader title={title} description={description}>
        <Button asChild variant="outline"><Link to="/dictionaries">返回快捷配置</Link></Button>
        {auth.hasPerm("dictionary:edit") && <Button disabled={locked} onClick={() => setEditing({
          type, code: "", name: "", remark: "", sort_order: 0, is_active: true,
        })}>新增{title}</Button>}
      </SourcePageHeader>
      <ReadOnlyNotice canEdit={auth.hasPerm("dictionary:edit")} subject={title} />
      <SourceDirectoryCard
        title={title}
        icon={BookOpen}
        description={list.data ? `本页 ${list.rows.length} 项` : "记录与总数未读取"}
        retry={list.reload}
        filters={
          <form onSubmit={event => { event.preventDefault(); setFilters(old => ({ ...old, keyword })) }}>
            <AdminFilters title="字典项筛选" primary={<>
              <div className="space-y-2"><label htmlFor="dictionary-search" className="text-sm font-medium">名称 / 编码</label><Input id="dictionary-search" value={keyword} onChange={event => setKeyword(event.target.value)} /></div>
              <Field field={{ key: "dictionary-status", label: "状态", type: "select", options: [["true", "启用"], ["false", "停用"]] }} value={filters.is_active} onChange={value => setFilters(old => ({ ...old, is_active: value }))} />
            </>} actions={<><Button type="submit">查询</Button><Button type="button" variant="ghost" onClick={() => { setKeyword(""); setFilters({ keyword: "", is_active: "" }) }}>清除筛选</Button></>} />
          </form>
        }
        locked={locked}
        empty="暂无字典项"
        rows={list.rows}
        snapshotAvailable={!!list.data}
        error={list.error || ""}
        loading={list.loading}
        columns={[
          { key: "name", label: "名称" },
          { key: "code", label: "稳定编码" },
          {
            key: "remark",
            label: "说明",
            className: "max-w-md whitespace-normal",
          },
          { key: "sort_order", label: "排序", align: "right" },
          {
            key: "is_active",
            label: "状态",
            align: "center",
            render: (row) => (
              <Status
                value={row.is_active}
                label={row.is_active ? "已启用" : "已停用"}
                readState={typeof row.is_active === "boolean" ? undefined : "unread"}
              />
            ),
          },
          {
            key: "actions",
            label: "操作",
            align: "right",
            render: (row) =>
              auth.hasPerm("dictionary:edit") && (
                <div className="flex gap-1">
                  <Button
                    actionTone="edit"
                    size="sm"
                    variant="outline"
                    disabled={locked}
                    onClick={() => setEditing({ ...row })}
                  >
                    编辑
                  </Button>
                  <Button
                    actionTone="reject"
                    size="sm"
                    variant="ghost"
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    disabled={locked}
                    onClick={() => setRemoving(row)}
                  >
                    删除
                  </Button>
                </div>
              ),
          },
        ]}
      />
      <Pagination
        page={list.page}
        total={list.total}
        snapshotAvailable={!!list.data}
        pageSize={list.pageSize}
        onChange={list.setPage}
        onPageSizeChange={list.setPageSize}
        loading={list.loading}
      />
      {editing && (
        <DictionaryEditor
          row={editing}
          locked={locked}
          title={title}
          type={type}
          onClose={() => setEditing(null)}
          onSaved={() => {
            void list.reload()
            void reloadCatalog()
          }}
        />
      )}
      {removing && (
        <MutationDialog
          title="删除字典项"
          readOnly={locked}
          submitVariant="destructive"
          description={`确认删除「${removing.name}」？已有业务引用时会拒绝删除，可改为停用。`}
          onClose={() => setRemoving(null)}
          onSubmit={async () => {
            await request({
              url: `/dictionary-items/${removing.id}/`,
              method: "DELETE",
              reviewPath: `/dictionaries?section=${type}`,
            })
            toast.success("字典项已删除")
            void list.reload()
            void reloadCatalog()
          }}
        />
      )}
    </div>
  </DataPageState>
}

function DictionaryEditor({
  row,
  locked,
  title,
  type,
  onClose,
  onSaved,
}: {
  row: Row
  locked: boolean
  title: string
  type: DictionaryType
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<Row>({ ...row })
  const [initialDraft] = useState(() => JSON.stringify(form))
  const [uncertain, setUncertain] = useState(false)
  return (
    <MutationDialog
      dirty={JSON.stringify(form) !== initialDraft}
      title={`${row.id ? "编辑" : "新增"}${title}`}
      description="名称与说明可调整；编码用于业务关联，保存后保持不变。"
      submitText="保存"
      readOnly={locked || uncertain}
      contentClassName="sm:max-w-2xl"
      confirmDiscard
      onClose={onClose}
      disabled={
        !form.name?.trim() ||
        !/^[A-Z][A-Z0-9_]{0,63}$/.test(form.code) ||
        !Number.isInteger(form.sort_order) ||
        form.sort_order < 0
      }
      onSubmit={async () => {
        const data = {
          name: form.name.trim(),
          remark: form.remark.trim(),
          sort_order: form.sort_order,
          is_active: form.is_active,
          ...(row.id ? {} : { type: form.type, code: form.code }),
        }
        try {
          await request({
            url: row.id ? `/dictionary-items/${row.id}/` : "/dictionary-items/",
            method: row.id ? "PATCH" : "POST",
            reviewPath: `/dictionaries?section=${type}`,
            data,
          })
        } catch (cause: any) {
          if (
            !cause.status ||
            cause.status >= 500 ||
            [408, 409, 429].includes(cause.status)
          )
            setUncertain(true)
          throw cause
        }
        toast.success("字典项已保存")
        onSaved()
      }}
    >
      {uncertain && (
        <Feedback message="保存结果待核对，请关闭并重新读取字典后再操作。" />
      )}
      <div className="admin-source-editor space-y-4">
        <FormSection title="标识与名称">
          <div className="grid grid-cols-2 gap-4">
            <Field
              field={{
                key: "dictionary-code",
                label: "编码",
                required: true,
                disabled: !!row.id,
                hint: "以大写字母开头，仅使用大写字母、数字和下划线，最多64位。",
              }}
              value={form.code}
              onChange={(value) => setForm((old) => ({ ...old, code: value }))}
            />
            <Field
              field={{ key: "dictionary-name", label: "名称", required: true }}
              value={form.name}
              onChange={(value) => setForm((old) => ({ ...old, name: value }))}
            />
          </div>
        </FormSection>
        <FormSection title="显示与说明">
          <div className="grid grid-cols-2 gap-4">
            <Field
              field={{
                key: "dictionary-sort",
                label: "排序",
                type: "number",
                min: 0,
                max: 2147483647,
                step: 1,
              }}
              value={form.sort_order}
              onChange={(value) =>
                setForm((old) => ({ ...old, sort_order: value }))
              }
            />
            <Field
              field={{
                key: "dictionary-active",
                label: "启用",
                type: "boolean",
              }}
              value={form.is_active}
              onChange={(value) =>
                setForm((old) => ({ ...old, is_active: value }))
              }
            />
            <Field
              field={{
                key: "dictionary-remark",
                label: "说明",
                type: "textarea",
                full: true,
              }}
              value={form.remark}
              onChange={(value) =>
                setForm((old) => ({ ...old, remark: value }))
              }
            />
          </div>
        </FormSection>
      </div>
    </MutationDialog>
  )
}
