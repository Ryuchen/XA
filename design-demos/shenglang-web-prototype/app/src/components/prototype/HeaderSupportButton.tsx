import { Headphones } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'

export function HeaderSupportButton() {
  return (
    <Button asChild variant="ghost" size="icon" className="size-11 shrink-0 rounded-full border border-primary/15 bg-primary/5 text-primary shadow-sm hover:bg-primary/10 hover:text-primary">
      <Link to="/support" aria-label="联系客服" title="联系客服">
        <Headphones className="size-5" />
      </Link>
    </Button>
  )
}
