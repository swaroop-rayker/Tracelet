/**
 * The primitives (docs/DESIGN.md §5). Pages compose these; they do not style elements
 * (DESIGN UI-1). Every primitive appears in the development gallery at /__design.
 */

export { Button, ButtonLink, CopyButton, IconButton, type ButtonVariant } from './Button';
export { Dialog } from './Dialog';
export {
  Badge,
  Card,
  Glyph,
  Identifier,
  KeyValue,
  Kbd,
  Legend,
  Secret,
  Tabs,
  Timestamp,
  type Dot,
  type KeyValueItem,
  type Swatch,
} from './display';
export {
  DataTable,
  RankedList,
  Sparkline,
  Stat,
  StatStrip,
  Stats,
  type Column,
  type Delta,
  type RankedRow,
  type Trend,
} from './data';
export {
  Alert,
  Banner,
  Callout,
  Empty,
  EmptyState,
  ErrorNotice,
  Loading,
  Skeleton,
  type SkeletonKind,
  type Tone,
} from './feedback';
export {
  Checkbox,
  Field,
  Input,
  SearchInput,
  SegmentedControl,
  Select,
  Submit,
  Switch,
  type Option,
} from './inputs';
export { Menu, MenuItem, MenuLabel, MenuSeparator, Popover, type TriggerProps } from './Popover';
export { InfoTip, Tooltip } from './Tooltip';
export { cssVars, cx, modKey, relativeTime } from './util';
