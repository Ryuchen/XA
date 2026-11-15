import { Feedback, Field, OptionPicker, useLoad, type Row } from "@/components/business"
import { request } from "@/api/request"

export default function QualificationFilters({
  value,
  onChange,
  service = false,
  embedded = false,
}: {
  value: Row
  onChange: (value: Row) => void
  service?: boolean
  embedded?: boolean
}) {
  const result = useLoad(
    async () => (await request({ url: "/qualification-options/" })).data
  )
  const data = result.data
  const scopes = (data?.scopes || []).filter(
    (scope: Row) =>
      (!value.provider_kind || scope.provider_kind === value.provider_kind) &&
      (!value.platform || scope.platform === value.platform) &&
      (!value.security_level || scope.security_level === value.security_level)
  )
  const grades = [
    ...new Map(
      scopes
        .flatMap((scope: Row) => scope.grades || [])
        .map((row: Row) => [row.value, row])
    ).values(),
  ] as Row[]
  function change(key: string, next: any) {
    onChange({
      ...value,
      [key]: next,
      ...(key === "provider_kind"
        ? { platform: "", security_level: "", grade: "" }
        : key === "platform"
          ? { security_level: "", grade: "" }
          : key === "security_level"
            ? { grade: "" }
            : {}),
    })
  }
  const fields = <>
      <div className="mt-4"><Feedback message={result.error} retry={result.reload} />{result.loading && !data && <p role="status" className="mb-3 text-xs text-muted-foreground">正在读取接单资格…</p>}</div>
      <fieldset disabled={result.loading || !!result.error} className={embedded ? "admin-filter-fields" : "mt-4 grid grid-cols-4 gap-3"}>
        {!service && (
          <OptionPicker
            resource="game-categories"
            label="可接游戏"
            value={value.game_category}
            onChange={(next) => change("game_category", next)}
          />
        )}
        <Field
          field={{
            key: "provider_kind",
            label: "接单方向",
            type: "select",
            options: (data?.kinds || []).map((row: Row) => [
              row.value,
              row.label,
            ]),
          }}
          value={value.provider_kind}
          onChange={(next) => change("provider_kind", next)}
        />
        <Field
          field={{
            key: "platform",
            label: "平台",
            type: "select",
            options: (data?.platforms || []).map((row: Row) => [
              row.value,
              row.label,
            ]),
          }}
          value={value.platform}
          onChange={(next) => change("platform", next)}
        />
        <Field
          field={{
            key: "security_level",
            label: "难度",
            type: "select",
            options: [
              ...new Set<string>(scopes.map((row: Row) => row.security_level)),
            ].map((key) => [
              key,
              (
                {
                  CONFIDENTIAL: "机密",
                  TOP_SECRET: "绝密",
                  ANY: "不限难度",
                } as Row
              )[key] || key,
            ]),
          }}
          value={value.security_level}
          onChange={(next) => change("security_level", next)}
        />
        <Field
          field={{
            key: "grade",
            label: "最高档位",
            type: "select",
            options: grades.map((row) => [row.value, row.label]),
          }}
          value={value.grade}
          onChange={(next) => change("grade", next)}
        />
        {!service && (
          <Field
            field={{
              key: "pass_tier",
              label: "通行证",
              type: "select",
              options: [
                ["BLACK", "黑金"],
                ["GOLD", "黄金"],
                ["SILVER", "白银"],
                ["BRONZE", "青铜"],
                ["NONE", "无通行证"],
              ],
            }}
            value={value.pass_tier}
            onChange={(next) => change("pass_tier", next)}
          />
        )}
      </fieldset>
    </>
  return embedded ? <div className="col-span-full" aria-label="接单资格筛选">{fields}</div> : (
    <details className="basis-full rounded-lg border p-3">
      <summary className="cursor-pointer text-sm font-medium">
        {service ? "接单资格筛选" : "游戏、接单资格与通行证筛选"}
      </summary>
      {fields}
    </details>
  )
}
