import { useEffect, useRef, useState } from "react"
import { getSession } from "@xa/shared"
import { toast } from "sonner"
import { request } from "@/api/request"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DataTable, Feedback, Field, MutationDialog, Pagination, useList, useLoad, type Row } from "@/components/business"
import { useAdminAuth } from "@/lib/auth"
import { enumLabel } from "@/lib/labels"
import { Status } from "@/components/Status"
import { ReadErrorNotice } from "@/components/page-data-state"

const gameNames: Record<string, string> = { DELTA: "三角洲行动", VALORANT: "瓦罗兰特", LEAGUE: "英雄联盟" }
const kindOptions: Array<[string, string]> = [["", "请选择"], ["GUARD", "护航"], ["COMPANION", "陪玩"], ["GRIND", "代肝"], ["RANK", "上分"]]
const platformOptions: Array<[string, string]> = [["", "未指定"], ["PC", "端游"], ["MOBILE", "手游"]]
const securityOptions: Array<[string, string]> = [["", "未指定"], ["ANY", "不限难度"], ["CONFIDENTIAL", "机密"], ["TOP_SECRET", "绝密"]]
const gradeOptions: Array<[string, string]> = [["", "未指定"], ["ENTERTAINMENT", "娱乐"], ["TECHNICAL", "技术"], ["MASTER", "大师"], ["KING", "王者"], ["LEGEND", "传奇"], ["GLORY", "荣耀"], ["STAR", "明星"], ["OPEN", "不分档"]]

type Scope = { provider_kind: string; platform: string; security_level: string; grade: string; gameplay_id: number | null; rate: number | "" }
type Draft = { game: string; companion: number | ""; grind: number | ""; scopes: Scope[] }
const emptyDraft = (): Draft => ({ game: "", companion: "", grind: "", scopes: [] })
const validRate = (value: number | "") => value === "" || (Number.isInteger(value) && value >= 0 && value <= 100)
const requiredRate = (value: number | "") => value !== "" && validRate(value)

export default function GameCommissionDraft({ catalog, error, onClose, onSaved }: { catalog: Row; error?: string; onClose: () => void; onSaved: () => void }) {
  const [selected, setSelected] = useState<Row | null>(null)
  const [coverageOpen, setCoverageOpen] = useState(false)
  return <>
    <Dialog open onOpenChange={(open) => { if (!open && !selected && !coverageOpen) onClose() }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl" onInteractOutside={(event) => { if (selected || coverageOpen) event.preventDefault() }}>
        <DialogHeader><DialogTitle>游戏抽成草稿</DialogTitle><DialogDescription>先为本店游戏类目填写草稿。当前草稿尚未用于订单计算。</DialogDescription></DialogHeader>
        <ReadErrorNotice error={error} retry={onSaved} hasSnapshot />
        <div><Button type="button" size="sm" variant="outline" onClick={() => setCoverageOpen(true)}>核对服务覆盖</Button></div>
        <div className="space-y-2">
          {!catalog.games?.length && <p className="text-sm text-muted-foreground">请先在服务商品中建立游戏类目。</p>}
          {(catalog.games || []).map((game: Row) => <div key={game.id} className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <div><div className="flex items-center gap-2 text-sm font-medium">{game.name}<Status value={game.is_active} label={typeof game.is_active === "boolean" ? game.is_active ? "已启用" : "已停用" : undefined} /></div>
              <div className="mt-2 text-xs text-muted-foreground">{game.game ? `${gameNames[game.game] || enumLabel(game.game)} · 草稿` : game.game === undefined ? <Status readState="unread" /> : <Status label="未配置" />}</div></div>
            <Button size="sm" variant="outline" onClick={() => setSelected(game)}>查看或填写</Button>
          </div>)}
        </div>
      </DialogContent>
    </Dialog>
    {selected && <GameCommissionEditor key={selected.id} game={selected} gameplays={catalog.gameplays || []}
      onClose={() => setSelected(null)} onSaved={() => { onSaved(); setSelected(null) }} />}
    {coverageOpen && <GameCommissionCoverage games={catalog.games || []} onClose={() => setCoverageOpen(false)} />}
  </>
}

function GameCommissionCoverage({ games, onClose }: { games: Row[]; onClose: () => void }) {
  const [filter, setFilter] = useState("all")
  const [gameId, setGameId] = useState("")
  const result = useList("config/game-commissions/coverage", { result: filter, game_category_id: gameId })
  const counts = (result.data as Row | null)?.counts
  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
      <DialogHeader><DialogTitle>服务抽成覆盖核对</DialogTitle><DialogDescription>
        仅核对当前启用服务与草稿的匹配情况；不代表抽成已生效或门店已满足开业条件。活动、礼物、体验单和加购仍需单独核对。
      </DialogDescription></DialogHeader>
      <Feedback message={result.error} retry={result.reload} />
      {counts && <p className="text-sm">启用服务 {counts.all} · 匹配草稿 {counts.matched} · 待处理 {counts.issues} · 特殊规则 {counts.special}</p>}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48"><Field field={{ key: "commission-coverage-game", label: "游戏类目", type: "select",
          options: [["", "全部游戏"], ...games.map((game): [number, string] => [game.id, game.name])] }}
          value={gameId} onChange={setGameId} /></div>
        <div className="min-w-48"><Field field={{ key: "commission-coverage-filter", label: "核对结果", type: "select",
          options: [["all", "全部"], ["issues", "待处理"], ["matched", "匹配草稿"], ["special", "特殊规则"]] }}
          value={filter} onChange={setFilter} /></div>
        <Button type="button" variant="outline" size="sm" disabled={result.loading} onClick={() => void result.reload()}>重新核对</Button>
      </div>
      <DataTable rows={result.rows} loading={result.loading} empty="没有符合条件的服务" columns={[
        { key: "name", label: "服务", render: (row) => <div><p>{row.name}</p><p className="text-xs text-muted-foreground">{row.game_name || "未关联游戏"}{row.gameplay_name ? ` · ${row.gameplay_name}` : ""}</p></div> },
        { key: "scope", label: "服务范围", render: (row) => [row.provider_kind, row.platform, row.security_level, row.grade].filter(Boolean).map(enumLabel).join(" · ") || "未设置" },
        { key: "message", label: "核对结果", render: (row) => <div><p>{row.message}</p>{row.code === "MATCHED_DRAFT" && <p className="text-xs text-muted-foreground">门店抽成 {row.rate}% · 草稿版本 {row.version}</p>}</div> },
      ]} />
      <Pagination page={result.page} total={result.total} pageSize={result.pageSize} onChange={result.setPage}
        onPageSizeChange={result.setPageSize} loading={result.loading} />
    </DialogContent>
  </Dialog>
}

function GameCommissionEditor({ game, gameplays, onClose, onSaved }: { game: Row; gameplays: Row[]; onClose: () => void; onSaved: () => void }) {
  const auth = useAdminAuth()
  const owner = useRef(getSession("admin").revision)
  const result = useLoad(async () => (await request<Row>({ url: `/config/game-commissions/${game.id}/` })).data)
  const [form, setForm] = useState<Draft>(emptyDraft)
  const [original, setOriginal] = useState("")
  const [blocked, setBlocked] = useState("")
  useEffect(() => {
    if (!result.data) return
    const data = result.data.configuration
    const next: Draft = data ? { game: data.game, companion: data.rules.companion ?? "", grind: data.rules.grind ?? "", scopes: data.rules.scopes || [] } : emptyDraft()
    setForm(next); setOriginal(JSON.stringify(next)); setBlocked("")
  }, [result.data])
  const dirty = !!original && JSON.stringify(form) !== original
  const valid = !!form.game && (form.game === "DELTA"
    ? form.scopes.length <= 300 && form.scopes.every((row) => !!row.provider_kind && requiredRate(row.rate))
    : validRate(form.companion) && validRate(form.grind))
  const disabled = result.loading || !!result.error || !!blocked || !result.data
  const changeScope = (index: number, patch: Partial<Scope>) => setForm((old) => ({ ...old,
    scopes: old.scopes.map((row, i) => i === index ? { ...row, ...patch } : row) }))
  if (!auth.hasPerm("dictionary:edit") && result.error && !result.data) return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
    <DialogContent><DialogHeader><DialogTitle>{game.name} · 游戏抽成草稿</DialogTitle>
      <DialogDescription>读取本店配置。</DialogDescription></DialogHeader>
      <Feedback message={result.error} retry={result.reload} />
    </DialogContent>
  </Dialog>
  return <MutationDialog title={`${game.name} · 游戏抽成草稿`}
    description="草稿仅供配置和核对，尚未用于订单计算。比例为门店抽成，留空表示未配置。"
    contentClassName="sm:max-w-5xl" submitText="保存草稿" onClose={onClose} confirmDiscard dirty={dirty}
    readOnly={!auth.hasPerm("dictionary:edit")} disabled={disabled || !valid || !dirty}
    onSubmit={async () => {
      if (!result.data || getSession("admin").revision !== owner.current) throw new Error("登录状态已变化，请重新打开配置。")
      const rules = form.game === "DELTA" ? { scopes: form.scopes } : {
        companion: form.companion === "" ? null : form.companion,
        grind: form.grind === "" ? null : form.grind,
      }
      try {
        await request({ url: `/config/game-commissions/${game.id}/`, method: "PUT",
          data: { game: form.game, rules, version: result.data.configuration?.version || 0 } })
        if (getSession("admin").revision !== owner.current) throw new Error("登录状态已变化，请重新打开配置核对。")
        toast.success("抽成草稿已保存，尚未生效")
        onSaved()
      } catch (cause: any) {
        if (cause.status === 409 || !cause.status || cause.status >= 500) setBlocked("保存结果或版本需要核对，请重新读取后再操作。")
        throw cause
      }
    }}>
    <Feedback message={result.error || blocked} />
    {(blocked || result.error) && <Button type="button" size="sm" variant="outline" disabled={result.loading} onClick={() => {
      if (dirty && !window.confirm("重新读取会清除尚未保存的填写内容，是否继续？")) return
      void result.reload()
    }}>重新读取草稿</Button>}
    {result.data && <>
      <Field field={{ key: "commission-game-kind", label: "游戏分类", type: "select", required: true,
        options: [["", "请选择"], ["DELTA", "三角洲行动"], ["VALORANT", "瓦罗兰特"], ["LEAGUE", "英雄联盟"]] }}
        value={form.game} onChange={(value) => {
          if (value !== form.game && form.game && !window.confirm("切换游戏分类会清除当前填写的规则，是否继续？")) return
          setForm({ ...emptyDraft(), game: value })
        }} />
      {form.game && form.game !== "DELTA" && <div className="grid gap-4 sm:grid-cols-2">
        <Field field={{ key: "commission-companion", label: "陪玩抽成（%）", type: "number", min: 0, max: 100, step: 1 }}
          value={form.companion} onChange={(value) => setForm((old) => ({ ...old, companion: value }))} />
        <Field field={{ key: "commission-grind", label: "代肝及上分抽成（%）", type: "number", min: 0, max: 100, step: 1 }}
          value={form.grind} onChange={(value) => setForm((old) => ({ ...old, grind: value }))} />
      </div>}
      {form.game === "DELTA" && <div className="space-y-4">
        <p className="text-xs text-muted-foreground">每行精确匹配方向、端、难度和档位；“全部玩法”不能与同范围的具体玩法重复。</p>
        {form.scopes.map((row, index) => <div key={index} className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between"><p className="text-sm font-medium">范围 {index + 1}</p>
            <Button type="button" size="sm" variant="ghost" onClick={() => setForm((old) => ({ ...old, scopes: old.scopes.filter((_, i) => i !== index) }))}>移除</Button></div>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field field={{ key: `scope-kind-${index}`, label: "服务方向", type: "select", options: kindOptions }} value={row.provider_kind} onChange={(value) => changeScope(index, { provider_kind: value })} />
            <Field field={{ key: `scope-platform-${index}`, label: "端", type: "select", options: platformOptions }} value={row.platform} onChange={(value) => changeScope(index, { platform: value })} />
            <Field field={{ key: `scope-security-${index}`, label: "难度", type: "select", options: securityOptions }} value={row.security_level} onChange={(value) => changeScope(index, { security_level: value })} />
            <Field field={{ key: `scope-grade-${index}`, label: "档位", type: "select", options: gradeOptions }} value={row.grade} onChange={(value) => changeScope(index, { grade: value })} />
            <Field field={{ key: `scope-gameplay-${index}`, label: "玩法", type: "select", options: [["", "全部玩法"], ...gameplays.map((item): [number, string] => [item.id, `${item.name}${item.is_archived || !item.is_active ? "（已停用）" : ""}`])] }} value={row.gameplay_id ?? ""} onChange={(value) => changeScope(index, { gameplay_id: value === "" ? null : Number(value) })} />
            <Field field={{ key: `scope-rate-${index}`, label: "抽成（%）", type: "number", min: 0, max: 100, step: 1 }} value={row.rate} onChange={(value) => changeScope(index, { rate: value })} />
          </div>
        </div>)}
        <Button type="button" variant="outline" size="sm" disabled={form.scopes.length >= 300} onClick={() => setForm((old) => ({ ...old, scopes: [...old.scopes, { provider_kind: "", platform: "", security_level: "", grade: "", gameplay_id: null, rate: "" }] }))}>添加范围</Button>
      </div>}
      {!valid && <p className="text-xs text-muted-foreground">请选择游戏分类，并填写有效的 0–100 整数比例；三角洲每条范围须填写方向和比例。</p>}
    </>}
  </MutationDialog>
}
