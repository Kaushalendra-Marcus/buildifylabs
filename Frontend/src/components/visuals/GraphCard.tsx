/**
 * GraphCard (F4) — `graph` visual (src/lib/schemas/visuals.ts). A Recharts
 * chart switched on `chart_type` (line / bar / pie / area). Spans two columns
 * in the grid (specs/14 §4.2). Dataset colours come from the design tokens so
 * both themes stay correct.
 *
 * InsightDeck rules applied here: one chart never uses more than five
 * colours (pie slices fold into an "Other" bucket), axis/legend text is
 * readable rather than muted, and the presentation variant gets a larger
 * canvas without changing the chart's data.
 */
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { GraphProps } from '../../lib/schemas/visuals';
import { formatCompactNumber } from '../../lib/format';
import { useEffectiveTheme } from '../../lib/theme-store';

const DATASET_COLORS = [
  'var(--accent)',
  'var(--success)',
  'var(--warning)',
  'var(--danger)',
  'var(--chart-pie-4)',
];

/** InsightDeck ceiling: never encode more than five series/categorical
 * colours in one chart. The fifth colour exists; a sixth would repeat. */
const MAX_SERIES = 5;
const MAX_PIE_SLICES = 5;

/** Donut ramp fallbacks (dark theme) — live values come from the
 * `--chart-pie-*` tokens so the ramp follows the theme. */
const PIE_COLOR_FALLBACKS = [
  '#ffbf48',
  '#c96a24',
  '#8f8a83',
  '#5f5a53',
  '#403b36',
];

interface PieSlice {
  name: string;
  value: number;
}

/** Fold a long tail into one honest "Other" slice so the donut never cycles
 * colours or presents two indistinguishable categories. */
function foldPieSlices(slices: PieSlice[]): PieSlice[] {
  if (slices.length <= MAX_PIE_SLICES) return slices;
  const ranked = [...slices].sort((a, b) => b.value - a.value);
  const head = ranked.slice(0, MAX_PIE_SLICES - 1);
  const tail = ranked.slice(MAX_PIE_SLICES - 1);
  return [
    ...head,
    {
      name: `Other (${tail.length} categories)`,
      value: tail.reduce((sum, slice) => sum + slice.value, 0),
    },
  ];
}

/** Theme-aware pie slice color: reads `--chart-pie-N` at render (reactive —
 * a theme switch re-renders via the store), falling back to the dark ramp
 * when the token is unavailable (e.g. jsdom tests). */
function pieColor(index: number): string {
  const slot = (index % PIE_COLOR_FALLBACKS.length) + 1;
  try {
    if (
      typeof window === 'undefined' ||
      typeof window.getComputedStyle !== 'function' ||
      typeof document === 'undefined'
    ) {
      return PIE_COLOR_FALLBACKS[index % PIE_COLOR_FALLBACKS.length];
    }
    const value = window
      .getComputedStyle(document.documentElement)
      .getPropertyValue(`--chart-pie-${slot}`)
      .trim();
    return value || PIE_COLOR_FALLBACKS[index % PIE_COLOR_FALLBACKS.length];
  } catch {
    return PIE_COLOR_FALLBACKS[index % PIE_COLOR_FALLBACKS.length];
  }
}

const TOOLTIP_STYLE = {
  background: 'var(--surface-raised)',
  border: '1px solid var(--border-default)',
  borderRadius: 10,
  padding: '9px 12px',
  boxShadow: 'var(--elevation-card)',
  fontSize: 12,
  color: 'var(--text-secondary)',
};

const TOOLTIP_LABEL_STYLE = {
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  marginBottom: 4,
};

/** Hover wash: a whisper of amber, never Recharts' default grey block. */
const BAR_CURSOR = {
  fill: 'color-mix(in srgb, var(--accent) 8%, transparent)',
  stroke: 'none',
};
const LINE_CURSOR = {
  stroke: 'color-mix(in srgb, var(--accent) 45%, transparent)',
  strokeDasharray: '4 4',
};

type Row = Record<string, string | number>;

function buildRows(props: GraphProps): Row[] {
  return props.labels.map((label, index) => {
    const row: Row = { label };
    for (const dataset of props.datasets) {
      row[dataset.name] = dataset.values[index] ?? 0;
    }
    return row;
  });
}

interface GraphCardProps {
  props: GraphProps;
  title?: string;
  presentation?: boolean;
}

export function GraphCard({
  props,
  title,
  presentation = false,
}: GraphCardProps) {
  const { chart_type, labels, datasets } = props;
  const data = buildRows(props);
  const chartHeight = presentation ? 440 : 240;
  const donutHeight = presentation ? 360 : 200;
  const chartLabel = title
    ? `${title} — ${chart_type} chart`
    : `${chart_type} chart`;
  // Re-render on any theme change (explicit choice or OS-driven) so the
  // baked pie ramp follows it; everything else here is live CSS vars.
  useEffectiveTheme();

  if (chart_type === 'pie') {
    const pieData = foldPieSlices(
      labels.map((label, index) => ({
        name: label,
        value: datasets[0]?.values[index] ?? 0,
      })),
    );
    const total = pieData.reduce((sum, slice) => sum + slice.value, 0);
    return (
      <div
        className={[
          'visual-graph',
          'visual-graph--donut',
          presentation ? 'visual-graph--presentation' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        role="img"
        aria-label={chartLabel}
      >
        <div className="visual-donut__chart">
          <ResponsiveContainer width="100%" height={donutHeight}>
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                innerRadius={presentation ? 100 : 54}
                outerRadius={presentation ? 148 : 80}
                paddingAngle={2}
                stroke="var(--surface-card)"
                strokeWidth={presentation ? 4 : 3}
              >
                {pieData.map((slice, index) => (
                  <Cell
                    key={`${slice.name}-${index}`}
                    fill={pieColor(index)}
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                formatter={(value) => Number(value).toLocaleString()}
              />
            </PieChart>
          </ResponsiveContainer>
          <div
            className="visual-donut__center"
            style={{ height: donutHeight }}
            aria-hidden="true"
          >
            <span className="visual-donut__total" title={total.toLocaleString()}>
              {formatCompactNumber(total)}
            </span>
            <span className="visual-donut__caption">total</span>
          </div>
        </div>
        <ul className="visual-donut__legend">
          {pieData.map((slice, index) => (
            <li key={`${slice.name}-${index}`}>
              <i aria-hidden="true" style={{ background: pieColor(index) }} />
              <span className="visual-donut__legend-name">{slice.name}</span>
              <span className="visual-donut__legend-value">
                {total > 0
                  ? `${((slice.value / total) * 100).toFixed(1)}%`
                  : '—'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const visibleDatasets = datasets.slice(0, MAX_SERIES);
  const hiddenSeriesCount = datasets.length - visibleDatasets.length;

  const axes = (
    <>
      <CartesianGrid
        strokeDasharray="4 6"
        stroke="var(--border-subtle)"
        vertical={false}
      />
      <XAxis
        dataKey="label"
        tick={{ fontSize: 12, fill: 'var(--text-secondary)' }}
        tickMargin={9}
        tickLine={false}
        axisLine={{ stroke: 'var(--border-default)' }}
      />
      <YAxis
        tick={{ fontSize: 12, fill: 'var(--text-secondary)' }}
        tickFormatter={(value: number) => formatCompactNumber(Number(value))}
        tickMargin={8}
        tickLine={false}
        axisLine={false}
        width={48}
      />
      <Legend
        iconType="circle"
        iconSize={8}
        wrapperStyle={{ fontSize: 12, paddingTop: 10 }}
      />
    </>
  );

  const tooltip = (cursor: typeof BAR_CURSOR | typeof LINE_CURSOR) => (
    <Tooltip
      contentStyle={TOOLTIP_STYLE}
      labelStyle={TOOLTIP_LABEL_STYLE}
      cursor={cursor}
      formatter={(value, name) => [
        Number(value).toLocaleString(),
        name,
      ]}
    />
  );

  return (
    <div
      className={[
        'visual-graph',
        presentation ? 'visual-graph--presentation' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      role="img"
      aria-label={chartLabel}
    >
      <ResponsiveContainer width="100%" height={chartHeight}>
        {chart_type === 'line' ? (
          <LineChart
            data={data}
            margin={{ top: 8, right: 12, bottom: 4, left: 0 }}
          >
            {axes}
            {tooltip(LINE_CURSOR)}
            {visibleDatasets.map((dataset, index) => (
              <Line
                key={`${dataset.name}-${index}`}
                type="monotone"
                dataKey={dataset.name}
                stroke={DATASET_COLORS[index % DATASET_COLORS.length]}
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
              />
            ))}
          </LineChart>
        ) : chart_type === 'bar' ? (
          <BarChart
            data={data}
            margin={{ top: 8, right: 12, bottom: 4, left: 0 }}
          >
            {axes}
            {tooltip(BAR_CURSOR)}
            {visibleDatasets.map((dataset, index) => (
              <Bar
                key={`${dataset.name}-${index}`}
                dataKey={dataset.name}
                fill={DATASET_COLORS[index % DATASET_COLORS.length]}
                radius={[6, 6, 0, 0]}
                maxBarSize={presentation ? 84 : 56}
              />
            ))}
          </BarChart>
        ) : (
          <AreaChart
            data={data}
            margin={{ top: 8, right: 12, bottom: 4, left: 0 }}
          >
            {axes}
            {tooltip(LINE_CURSOR)}
            {visibleDatasets.map((dataset, index) => (
              <Area
                key={`${dataset.name}-${index}`}
                type="monotone"
                dataKey={dataset.name}
                stroke={DATASET_COLORS[index % DATASET_COLORS.length]}
                strokeWidth={2.5}
                fill={DATASET_COLORS[index % DATASET_COLORS.length]}
                fillOpacity={0.18}
              />
            ))}
          </AreaChart>
        )}
      </ResponsiveContainer>
      {hiddenSeriesCount > 0 && (
        <p className="visual-graph__note">
          Showing {MAX_SERIES} of {datasets.length} series to keep the chart
          readable. Filter the data to compare the rest.
        </p>
      )}
    </div>
  );
}
