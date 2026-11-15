import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from "react"

/** Label custom SaaS forms without changing the existing schema-driven Field. */
export function SaasField({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  const id = useId()
  if (isValidElement(children) && children.type !== "div") {
    const input = children as ReactElement<{ id?: string }>
    const controlId = input.props.id || id
    return (
      <div className="grid gap-2">
        <label className="text-sm font-medium" htmlFor={controlId}>
          {label}
        </label>
        {cloneElement(input, { id: controlId })}
      </div>
    )
  }
  return (
    <div className="grid gap-2" role="group" aria-labelledby={id}>
      <p id={id} className="text-sm font-medium">
        {label}
      </p>
      {children}
    </div>
  )
}
