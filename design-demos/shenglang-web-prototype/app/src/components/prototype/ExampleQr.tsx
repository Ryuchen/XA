import { cn } from '@/lib/utils'

export function ExampleQr({ large = false }: { large?: boolean }) {
  const modules = [[42, 8], [50, 8], [66, 8], [42, 16], [58, 16], [74, 16], [8, 42], [16, 50], [42, 42], [50, 50], [58, 42], [66, 50], [74, 42], [82, 50], [42, 66], [50, 74], [66, 66], [74, 74], [82, 66], [58, 82]]
  return <svg viewBox="0 0 96 96" className={cn('block', large ? 'size-64' : 'size-28')} role="img" aria-label="不可扫码的企业微信客服二维码示意图"><rect width="96" height="96" fill="white" /><g fill="#020617"><path d="M6 6h24v24H6zm4 4v16h16V10zm4 4h8v8h-8zM66 6h24v24H66zm4 4v16h16V10zm4 4h8v8h-8zM6 66h24v24H6zm4 4v16h16V70zm4 4h8v8h-8z" fillRule="evenodd" />{modules.map(([x, y], index) => <rect key={index} x={x} y={y} width="6" height="6" />)}</g><rect x="38" y="38" width="20" height="20" rx="6" fill="#2563EB" /><path d="M44 48v4m4-8v8m4-11v14m4-10v6" stroke="white" strokeWidth="2.5" strokeLinecap="round" /></svg>
}
