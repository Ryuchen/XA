import * as React from "react"
import * as Radix from "@radix-ui/react-icons"
import * as Lucide from "lucide-react"

/** Controls retain Radix artwork. Business symbols share the sidebar's Lucide strokes. */
export type LucideCompatibleProps = Omit<React.SVGProps<SVGSVGElement>, "children"> & {
  size?: number | string
}
export type LucideIcon = React.ForwardRefExoticComponent<
  LucideCompatibleProps & React.RefAttributes<SVGSVGElement>
>
type RadixComponent = typeof Radix.CheckIcon

function businessIcon(Icon: Lucide.LucideIcon): LucideIcon {
  const Wrapped = React.forwardRef<SVGSVGElement, LucideCompatibleProps>(
    ({ size, width, height, ...props }, ref) => (
      <Icon
        ref={ref}
        data-icon="lucide"
        aria-hidden={props["aria-label"] ? undefined : true}
        focusable="false"
        strokeWidth={1.75}
        width={size ?? width ?? 24}
        height={size ?? height ?? width ?? 24}
        {...props}
      />
    )
  )
  Wrapped.displayName = Icon.displayName
  return Wrapped
}

function adapt(Icon: RadixComponent): LucideIcon {
  const Wrapped = React.forwardRef<SVGSVGElement, LucideCompatibleProps>(
    ({ size, width, height, ...props }, ref) => (
      <Icon
        ref={ref}
        data-icon="radix"
        aria-hidden={props["aria-label"] ? undefined : true}
        focusable="false"
        width={size ?? width ?? 24}
        height={size ?? height ?? width ?? 24}
        {...props}
      />
    )
  )
  Wrapped.displayName = `Radix.${Icon.displayName || Icon.name}`
  return Wrapped
}

export const AlertCircle = adapt(Radix.ExclamationTriangleIcon)
export const Archive = adapt(Radix.ArchiveIcon)
export const ArchiveRestore = adapt(Radix.UpdateIcon)
export const ArrowDown = adapt(Radix.ArrowDownIcon)
export const ArrowDownLeft = businessIcon(Lucide.ArrowDownLeft)
export const ArrowLeftRight = businessIcon(Lucide.ArrowLeftRight)
export const ArrowRight = adapt(Radix.ArrowRightIcon)
export const ArrowUp = adapt(Radix.ArrowUpIcon)
export const ArrowUpRight = businessIcon(Lucide.ArrowUpRight)
export const Award = adapt(Radix.BadgeIcon)
export const BadgeCheck = adapt(Radix.CheckCircledIcon)
export const Banknote = businessIcon(Lucide.Banknote)
export const Bell = adapt(Radix.BellIcon)
export const BookOpen = adapt(Radix.ReaderIcon)
export const Boxes = adapt(Radix.StackIcon)
export const BriefcaseBusiness = businessIcon(Lucide.BriefcaseBusiness)
export const Building2 = adapt(Radix.HomeIcon)
export const Calculator = businessIcon(Lucide.Calculator)
export const CalendarCheck2 = businessIcon(Lucide.CalendarCheck2)
export const CalendarClock = businessIcon(Lucide.CalendarClock)
export const CalendarDays = businessIcon(Lucide.CalendarDays)
export const Check = adapt(Radix.CheckIcon)
export const CheckCircle2 = adapt(Radix.CheckCircledIcon)
export const ChevronDown = adapt(Radix.ChevronDownIcon)
export const ChevronLeft = adapt(Radix.ChevronLeftIcon)
export const ChevronRight = adapt(Radix.ChevronRightIcon)
export const ChevronUp = adapt(Radix.ChevronUpIcon)
export const ChevronsUpDown = adapt(Radix.CaretSortIcon)
export const CircleAlert = adapt(Radix.ExclamationTriangleIcon)
export const CircleCheckIcon = adapt(Radix.CheckCircledIcon)
export const CircleHelp = adapt(Radix.QuestionMarkCircledIcon)
export const CircleMinus = businessIcon(Lucide.CircleMinus)
export const ClipboardCheck = businessIcon(Lucide.ClipboardCheck)
export const ClipboardList = businessIcon(Lucide.ClipboardList)
export const Clock = businessIcon(Lucide.Clock)
export const Clock3 = businessIcon(Lucide.Clock3)
export const Coins = businessIcon(Lucide.Coins)
export const Copy = adapt(Radix.CopyIcon)
export const CreditCard = businessIcon(Lucide.CreditCard)
export const Crown = adapt(Radix.StarFilledIcon)
export const ExternalLink = adapt(Radix.ExternalLinkIcon)
export const Eye = adapt(Radix.EyeOpenIcon)
export const EyeOff = adapt(Radix.EyeNoneIcon)
export const FileCheck2 = adapt(Radix.FileTextIcon)
export const FileClock = adapt(Radix.CounterClockwiseClockIcon)
export const FileText = adapt(Radix.FileTextIcon)
export const Filter = adapt(Radix.MixerHorizontalIcon)
export const Gamepad2 = businessIcon(Lucide.Gamepad2)
export const Gift = businessIcon(Lucide.Gift)
export const Headphones = businessIcon(Lucide.Headphones)
export const Headset = businessIcon(Lucide.Headset)
export const History = adapt(Radix.CounterClockwiseClockIcon)
export const ImagePlus = adapt(Radix.ImageIcon)
export const Images = adapt(Radix.ImageIcon)
export const InfoIcon = adapt(Radix.InfoCircledIcon)
export const KeyRound = adapt(Radix.LockClosedIcon)
export const Landmark = businessIcon(Lucide.Landmark)
export const Layers3 = businessIcon(Lucide.Layers3)
export const LayoutDashboard = adapt(Radix.DashboardIcon)
export const Link2 = businessIcon(Lucide.Link2)
export const ListFilter = adapt(Radix.ListBulletIcon)
export const ListOrdered = adapt(Radix.ListBulletIcon)
export const ListTree = adapt(Radix.ListBulletIcon)
export const Loader2Icon = adapt(Radix.ReloadIcon)
export const LoaderCircle = adapt(Radix.UpdateIcon)
export const LockKeyhole = businessIcon(Lucide.LockKeyhole)
export const LogOut = adapt(Radix.ExitIcon)
export const Medal = businessIcon(Lucide.Medal)
export const Megaphone = businessIcon(Lucide.Megaphone)
export const MessageCircle = adapt(Radix.ChatBubbleIcon)
export const MessageCircleMore = adapt(Radix.ChatBubbleIcon)
export const MessageSquareText = adapt(Radix.ChatBubbleIcon)
export const Moon = adapt(Radix.MoonIcon)
export const OctagonXIcon = adapt(Radix.CrossCircledIcon)
export const Package = adapt(Radix.BoxIcon)
export const PackageCheck = businessIcon(Lucide.PackageCheck)
export const PanelLeftClose = adapt(Radix.PinLeftIcon)
export const PanelLeftOpen = adapt(Radix.PinRightIcon)
export const Percent = adapt(Radix.ValueIcon)
export const Pin = adapt(Radix.DrawingPinIcon)
export const PinOff = adapt(Radix.DrawingPinFilledIcon)
export const Plus = adapt(Radix.PlusIcon)
export const QrCode = adapt(Radix.GridIcon)
export const Radio = adapt(Radix.RadiobuttonIcon)
export const ReceiptText = businessIcon(Lucide.ReceiptText)
export const RefreshCw = adapt(Radix.ReloadIcon)
export const RotateCcw = adapt(Radix.RotateCounterClockwiseIcon)
export const Search = adapt(Radix.MagnifyingGlassIcon)
export const Send = businessIcon(Lucide.Send)
export const Settings2 = adapt(Radix.GearIcon)
export const ShieldAlert = adapt(Radix.ExclamationTriangleIcon)
export const ShieldCheck = businessIcon(Lucide.ShieldCheck)
export const Smartphone = adapt(Radix.MobileIcon)
export const Sparkles = adapt(Radix.MagicWandIcon)
export const Store = adapt(Radix.HomeIcon)
export const Sun = adapt(Radix.SunIcon)
export const Trash2 = adapt(Radix.TrashIcon)
export const TriangleAlertIcon = adapt(Radix.ExclamationTriangleIcon)
export const Trophy = adapt(Radix.StarFilledIcon)
export const UserCheck = businessIcon(Lucide.UserCheck)
export const UserRound = businessIcon(Lucide.UserRound)
export const UsersRound = businessIcon(Lucide.UsersRound)
export const Volume2 = adapt(Radix.SpeakerLoudIcon)
export const VolumeX = adapt(Radix.SpeakerOffIcon)
export const WalletCards = businessIcon(Lucide.WalletCards)
export const X = adapt(Radix.Cross1Icon)
export const BanknoteArrowDown = businessIcon(Lucide.BanknoteArrowDown)
export const ChartLine = businessIcon(Lucide.ChartLine)
export const ClipboardPlus = businessIcon(Lucide.ClipboardPlus)
export const GraduationCap = businessIcon(Lucide.GraduationCap)
export const HandCoins = businessIcon(Lucide.HandCoins)
export const Handshake = businessIcon(Lucide.Handshake)
export const Hourglass = businessIcon(Lucide.Hourglass)
export const ScrollText = businessIcon(Lucide.ScrollText)
export const TrendingUp = businessIcon(Lucide.TrendingUp)
export const Wallet = businessIcon(Lucide.Wallet)
