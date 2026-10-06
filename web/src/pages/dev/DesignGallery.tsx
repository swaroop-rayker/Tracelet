/**
 * The development-only component gallery (DESIGN §11 Phase 1): every primitive in every
 * state, for review in all three themes. Registered only when `import.meta.env.DEV`, so the
 * production build does not contain it.
 */

import { useState } from 'react';
import {
  Alert,
  Badge,
  Banner,
  Button,
  ButtonLink,
  Card,
  Checkbox,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  IconButton,
  Identifier,
  InfoTip,
  Kbd,
  KeyValue,
  Legend,
  Loading,
  Menu,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  RankedList,
  SearchInput,
  SegmentedControl,
  Select,
  Glyph,
  Stat,
  StatStrip,
  Stats,
  Switch,
  Tabs,
  Timestamp,
  type Dot,
} from '@/components/ui';
import { THEMES, applyTheme, type ThemeName } from '@/theme';

const DOTS: readonly Dot[] = [
  'human',
  'unknown',
  'crawler',
  'datacenter',
  'bot',
  'spam',
  'spoofed',
];

interface SampleRow {
  readonly id: string;
  readonly time: string;
  readonly cls: string;
  readonly dot: Dot;
  readonly where: string;
  readonly n: number;
}

const SAMPLE_ROWS: readonly SampleRow[] = [
  { id: '1', time: '09:58', cls: 'Human', dot: 'human', where: 'Mumbai, Maharashtra · 50%', n: 3 },
  { id: '2', time: '09:41', cls: 'Bot', dot: 'bot', where: 'Ashburn, Virginia · 92%', n: 12 },
  { id: '3', time: '08:12', cls: 'Human', dot: 'human', where: 'Bengaluru, Karnataka ✓', n: 1 },
];

const TREND = {
  values: [3, 5, 4, 8, 6, null, 7, 9, 6, 8],
  spoken: 'Over 10 days: low 3, high 9.',
};

export default function DesignGallery(): React.JSX.Element {
  const [theme, setTheme] = useState<ThemeName>('semi_dark');
  const [seg, setSeg] = useState('day');
  const [tab, setTab] = useState('shape');
  const [checked, setChecked] = useState(true);
  const [on, setOn] = useState(false);
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [select, setSelect] = useState('30d');
  const [dialog, setDialog] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [sort, setSort] = useState('newest');
  const now = new Date().toISOString();

  return (
    <div className="page">
      <header className="gallery-head">
        <div>
          <h1 className="t-title">Design system</h1>
          <p className="t-secondary">
            Every primitive in docs/DESIGN.md §5, in every state. Development only.
          </p>
        </div>
        <SegmentedControl
          label="Theme"
          value={theme}
          options={THEMES.map((t) => ({ value: t.name, label: t.label }))}
          onChange={(value) => {
            const next = THEMES.find((t) => t.name === value)?.name ?? 'semi_dark';
            setTheme(next);
            applyTheme(next);
          }}
        />
      </header>

      <Card title="Buttons" description="One primary per view. Verbs on buttons.">
        <div className="gallery-row">
          <Button variant="primary" icon="Add">
            New geofence
          </Button>
          <Button icon="Download">Export CSV</Button>
          <Button variant="ghost">Cancel</Button>
          <Button variant="danger">Delete geofence</Button>
          <Button busy busyLabel="Sending…">
            Send test message
          </Button>
          <Button disabled>Disabled</Button>
          <Button size="sm" icon="Filter">
            Small
          </Button>
          <ButtonLink href="#" icon="External" variant="ghost">
            A link as a button
          </ButtonLink>
          <IconButton icon="Help" label="Help" />
          <IconButton icon="More" label="More actions" />
          <IconButton icon="Copy" label="Copy" size="sm" />
        </div>
      </Card>

      <Card title="Inputs">
        <div className="gallery-grid">
          <Field label="Name" value={text} onChange={setText} hint="Shown in alerts." />
          <Field
            label="With an error"
            value="Karnataka"
            onChange={() => undefined}
            error="A geofence with this name already exists."
          />
          <SearchInput
            label="Search visits"
            value={query}
            onChange={setQuery}
            placeholder="ISP, city, browser, link…"
            shortcut="/"
          />
          <Select
            label="Period"
            hideLabel={false}
            value={select}
            onChange={setSelect}
            options={[
              { value: '24h', label: 'Last 24 hours' },
              { value: '30d', label: 'Last 30 days' },
              { value: '90d', label: 'Last 90 days' },
            ]}
          />
        </div>
        <div className="gallery-row">
          <SegmentedControl
            label="Bucket"
            value={seg}
            onChange={setSeg}
            options={[
              { value: 'day', label: 'Day' },
              { value: 'hour', label: 'Hour' },
              { value: 'week', label: 'Week', disabled: true },
            ]}
          />
          <Checkbox label="Include automated traffic" checked={checked} onChange={setChecked} />
          <Switch label="Active" checked={on} onChange={setOn} />
          <span>
            Press <Kbd>Ctrl</Kbd> <Kbd>K</Kbd>
          </span>
        </div>
      </Card>

      <Card title="Badges and markers">
        <div className="gallery-row">
          {DOTS.map((d) => (
            <Badge key={d} dot={d}>
              {d}
            </Badge>
          ))}
        </div>
        <div className="gallery-row">
          <Badge tone="ok">Delivered</Badge>
          <Badge tone="warn">Retrying (3/8)</Badge>
          <Badge tone="error">Dead-lettered</Badge>
          <Badge tone="info">Confirmed</Badge>
          <Identifier value="01J9X4TQ7B2M5ZK8R1C3V6N0PW" label="Copy visit id" />
          <Timestamp iso={now} zone="Asia/Kolkata" />
          <span>
            <Glyph name="DeviceMobile" />
            Mobile
          </span>
          <span>
            <Glyph name="NetBroadband" />
            Broadband
          </span>
          <span>
            <Glyph name="InApp" />
            instagram
          </span>
          <span>
            Stage mix
            <InfoTip term="stage mix">
              What share of requests were enriched by the browser, captured server-side only, or
              rate-limited.
            </InfoTip>
          </span>
        </div>
        <Legend
          label="Series"
          items={[1, 2, 3, 4, 5, 6].map((i) => ({
            label: `Series ${String(i)}`,
            swatch: i as 1 | 2 | 3 | 4 | 5 | 6,
          }))}
        />
      </Card>

      <Stats label="Example KPIs">
        <Stat
          label="Visits"
          value="128.4K"
          exact="128,421"
          delta={{ direction: 'up', text: '12%', judgement: 'neutral' }}
          comparison="vs previous 30 days"
          hint="Every request to a tracking link in the period, automated traffic excluded."
          trend={TREND}
        />
        <Stat
          label="Human share"
          value="90.2%"
          delta={{ direction: 'up', text: '4.5 pts', judgement: 'good' }}
          comparison="vs previous"
        />
        <Stat
          label="Automated share"
          value="9.8%"
          delta={{ direction: 'down', text: '4.5 pts', judgement: 'good' }}
          comparison="vs previous"
        />
        <Stat label="Geofence hit rate" value="—" note="Geofencing arrives in M6" />
      </Stats>
      <StatStrip
        label="Secondary figures"
        items={[
          { key: 'a', label: 'Automated share', value: '9.8%' },
          { key: 'b', label: 'Location consent', value: '0.0%', trend: TREND },
          { key: 'c', label: 'Geofence hit rate', value: '—', hint: 'M6' },
        ]}
      />

      <div className="grid-2">
        <Card title="Ranked list" description="Click a row to filter by it.">
          <RankedList
            caption="Visits by state"
            labelHeader="State"
            onSelect={() => undefined}
            rows={[
              { key: 'MH', label: 'Maharashtra', count: 37, share: 0.257 },
              { key: 'KA', label: 'Karnataka', count: 20, share: 0.139 },
              { key: 'DL', label: 'Delhi', count: 19, share: 0.132 },
              { key: 'TN', label: 'Tamil Nadu', count: 17, share: 0.118 },
              { key: 'other', label: 'Other', count: 44, share: 0.306, muted: true },
              { key: 'unknown', label: 'Unknown', count: 7, share: 0.049, muted: true },
            ]}
          />
        </Card>
        <Card
          title="Data table"
          actions={
            <Menu label="Sort" triggerLabel="Newest first" size="sm">
              {(close) => (
                <>
                  <MenuLabel>Sort by</MenuLabel>
                  {['newest', 'oldest'].map((s) => (
                    <MenuItem
                      key={s}
                      checked={sort === s}
                      onSelect={() => {
                        setSort(s);
                        close();
                      }}
                    >
                      {s === 'newest' ? 'Newest first' : 'Oldest first'}
                    </MenuItem>
                  ))}
                  <MenuSeparator />
                  <MenuItem icon="Download" hint="CSV" onSelect={close}>
                    Export
                  </MenuItem>
                  <MenuItem danger icon="Error" onSelect={close}>
                    Delete…
                  </MenuItem>
                </>
              )}
            </Menu>
          }
        >
          <DataTable
            caption="Recent visits"
            rowKey={(r) => r.id}
            columns={[
              { key: 'time', header: 'Time', render: (r) => r.time, className: 'tabular' },
              {
                key: 'class',
                header: 'Class',
                render: (r) => <Badge dot={r.dot}>{r.cls}</Badge>,
              },
              { key: 'where', header: 'Location', render: (r) => r.where },
              { key: 'n', header: 'Visits', render: (r) => r.n.toLocaleString(), numeric: true },
            ]}
            rows={SAMPLE_ROWS}
          />
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Loading" description="Skeletons shaped like the content.">
          <div className="gallery-grid">
            <Loading kind="kpi" label="Loading KPIs" />
            <Loading kind="list" label="Loading a list" />
            <Loading kind="chart" label="Loading a chart" />
            <Loading kind="text" label="Loading text" />
          </div>
        </Card>
        <Card title="Empty, alerts, banner">
          <EmptyState
            title="No visits in this period"
            reason="Try a longer period, or include automated traffic."
            action={<Button size="sm">Show last 90 days</Button>}
          />
          <div className="stack">
            <Alert tone="info" title="Check Telegram">
              A six-digit code was sent to the chat.
            </Alert>
            <Alert tone="ok" title="Password changed" />
            <Alert tone="warn" title="Running low">
              Two recovery codes left.
            </Alert>
            <Alert tone="error" title="Problem">
              The server took too long to answer.
            </Alert>
          </div>
        </Card>
      </div>
      <Banner tone="warn" action={<Button size="sm">Details</Button>}>
        Degraded: the geo database is 41 days old. Capture and redirects are unaffected.
      </Banner>

      <Card title="Overlays and navigation">
        <div className="gallery-row">
          <Button
            onClick={() => {
              setDialog(true);
            }}
          >
            Open dialog
          </Button>
          <Button
            onClick={() => {
              setDrawer(true);
            }}
          >
            Open drawer
          </Button>
          <Menu label="More actions" icon="More" iconOnly align="end">
            {(close) => (
              <>
                <MenuItem icon="Copy" onSelect={close}>
                  Copy link to this chart
                </MenuItem>
                <MenuItem icon="Download" onSelect={close}>
                  Download CSV
                </MenuItem>
              </>
            )}
          </Menu>
        </div>
        <Tabs
          label="Geofence"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: 'shape', label: 'Shape' },
            { value: 'settings', label: 'Settings' },
            { value: 'history', label: 'Matches' },
          ]}
        />
        <KeyValue
          items={[
            { key: 'a', label: 'Location', value: 'Mumbai, Maharashtra, India · 50%' },
            { key: 'b', label: 'Device', value: 'Chrome 131 · Android 14' },
            { key: 'c', label: 'Network', value: 'Reliance Jio (AS55836)' },
          ]}
        />
      </Card>

      <Dialog
        open={dialog}
        onClose={() => {
          setDialog(false);
        }}
        title="Delete geofence"
        size="sm"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setDialog(false);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setDialog(false);
              }}
            >
              Delete geofence
            </Button>
          </>
        }
      >
        <p>“Karnataka” will stop matching visits. Visits it already matched keep their record.</p>
      </Dialog>
      <Dialog
        open={drawer}
        onClose={() => {
          setDrawer(false);
        }}
        title="Visit at 09:58, 2 Oct"
        drawer
      >
        <KeyValue
          items={[
            { key: 'a', label: 'Location', value: 'Mumbai, Maharashtra, India · 50%' },
            { key: 'b', label: 'Class', value: <Badge dot="human">Human</Badge> },
          ]}
        />
      </Dialog>
    </div>
  );
}
