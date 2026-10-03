/**
 * The icon registry (DESIGN §4.8, ADR-0019): every icon the product uses, under a product
 * name. Pages import `Icon.Visits`, never `lucide-react` directly, so a glyph is swapped in one
 * place and only the icons named here are bundled (Lucide tree-shakes per icon).
 *
 * Rendering rules live in the primitives, not here: 16 px in controls and tables, 18 px in the
 * navigation, a 1.5 px stroke, `currentColor` only, and never an icon without a visible label
 * or an `aria-label` (DESIGN §4.8). Icons are decorative to assistive technology by default
 * (`aria-hidden`); the label beside them carries the meaning.
 */

import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpRight,
  BellRing,
  CalendarDays,
  ChartColumn,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  CircleX,
  Copy,
  Crosshair,
  Download,
  Ellipsis,
  Globe,
  Hexagon,
  Info,
  Keyboard,
  LayoutDashboard,
  Link2,
  ListFilter,
  LogOut,
  Menu,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Rows3,
  Search,
  Settings,
  ShieldAlert,
  SunMoon,
  Table2,
  TriangleAlert,
  Upload,
  Workflow,
  X,
  type LucideIcon,
} from 'lucide-react';

export const Icon = {
  // Navigation (DESIGN §9.1)
  Overview: LayoutDashboard,
  Visits: Rows3,
  Geography: Globe,
  Breakdowns: ChartColumn,
  Inference: Workflow,
  Detection: ShieldAlert,
  Geofences: Hexagon,
  Alerts: BellRing,
  Health: Activity,
  Account: Settings,
  // Shell and actions
  Search,
  Help: CircleHelp,
  Shortcuts: Keyboard,
  Collapse: PanelLeftClose,
  Expand: PanelLeftOpen,
  Menu,
  Close: X,
  Chevron: ChevronDown,
  Next: ChevronRight,
  Previous: ChevronLeft,
  Back: ArrowLeft,
  More: Ellipsis,
  Filter: ListFilter,
  Add: Plus,
  Copy,
  Check,
  Download,
  Upload,
  External: ArrowUpRight,
  Calendar: CalendarDays,
  Link: Link2,
  Table: Table2,
  Theme: SunMoon,
  SignOut: LogOut,
  Locate: Crosshair,
  // State markers
  Info,
  Warn: TriangleAlert,
  Error: CircleX,
  Up: ArrowUp,
  Down: ArrowDown,
  Flat: Minus,
} as const satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof Icon;
