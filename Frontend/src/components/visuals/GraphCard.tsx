/**
 * GraphCard (F4) — `graph` visual (src/lib/schemas/visuals.ts). A Recharts
 * chart switched on `chart_type` (line / bar / pie / area). Spans two columns
 * in the grid (specs/14 §4.2). Dataset colours come from the design tokens so
 * both themes stay correct.
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

const DATASET_COLORS = [
  'var(--accent)',
  'var(--success)',
  'var(--warning)',
  'var(--danger)',
];

/** Donut ramp: brand amber first, then ember, then receding warm neutrals. */
const PIE_COLORS = [
  '#ffbf48',
  '#c96a24',
  '#8f8a83',
  '#5f5a53',
  '#403b36',
  '#2e2a26',
];

const TOOLTIP_STYLE = {
  background: 'var(--surface-raised)',
  border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)',
  borderRadius: 8,
  fontSize: 12,
  color: 'var(--text-secondary)',
};

const TOOLTIP_LABEL_STYLE = {
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
};

/** Hover wash: a whisper of amber, never Recharts' default grey block. */
const BAR_CURSOR = { fill: 'rgba(255, 191, 72, 0.08)', stroke: 'none' };
const LINE_CURSOR = { stroke: 'rgba(255, 191, 72, 0.45)', strokeDasharray: '4 4' };

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

export function GraphCard({ props }: { props: GraphProps }) {
  const { chart_type, labels, datasets } = props;
  const data = buildRows(props);

  if (chart_type === 'pie') {
    const pieData = labels.map((label, index) => ({
      name: label,
      value: datasets[0]?.values[index] ?? 0,
    }));
    const total = pieData.reduce((sum, slice) => sum + slice.value, 0);
    return (
      <div className="visual-graph visual-graph--donut" role="img" aria-label={`${chart_type} chart`}>
        <div className="visual-donut__chart">
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                innerRadius={54}
                outerRadius={80}
                paddingAngle={2}
                stroke="var(--surface-card)"
                strokeWidth={3}
              >
                {pieData.map((slice, index) => (
                  <Cell
                    key={slice.name}
                    fill={PIE_COLORS[index % PIE_COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
            </PieChart>
          </ResponsiveContainer>
          <div className="visual-donut__center" aria-hidden="true">
            <span className="visual-donut__total" title={total.toLocaleString()}>
              {formatCompactNumber(total)}
            </span>
            <span className="visual-donut__caption">total</span>
          </div>
        </div>
        <ul className="visual-donut__legend">
          {pieData.map((slice, index) => (
            <li key={slice.name}>
              <i
                aria-hidden="true"
                style={{ background: PIE_COLORS[index % PIE_COLORS.length] }}
              />
              <span className="visual-donut__legend-name">{slice.name}</span>
              <span className="visual-donut__legend-value">
                {total > 0 ? `${((slice.value / total) * 100).toFixed(1)}%` : '—'}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const axes = (
    <>
      <CartesianGrid
        strokeDasharray="3 3"
        stroke="var(--text-muted)"
        strokeOpacity={0.4}
      />
      <XAxis
        dataKey="label"
        tick={{ fontSize: 12, fill: 'var(--text-muted)' }}
      />
      <YAxis tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  );

  const tooltip = (cursor: typeof BAR_CURSOR | typeof LINE_CURSOR) => (
    <Tooltip
      contentStyle={TOOLTIP_STYLE}
      labelStyle={TOOLTIP_LABEL_STYLE}
      cursor={cursor}
    />
  );

  return (
    <div className="visual-graph" role="img" aria-label={`${chart_type} chart`}>
      <ResponsiveContainer width="100%" height={220}>
        {chart_type === 'line' ? (
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            {axes}
            {tooltip(LINE_CURSOR)}
            {datasets.map((dataset, index) => (
              <Line
                key={dataset.name}
                type="monotone"
                dataKey={dataset.name}
                stroke={DATASET_COLORS[index % DATASET_COLORS.length]}
                dot={false}
              />
            ))}
          </LineChart>
        ) : chart_type === 'bar' ? (
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            {axes}
            {tooltip(BAR_CURSOR)}
            {datasets.map((dataset, index) => (
              <Bar
                key={dataset.name}
                dataKey={dataset.name}
                fill={DATASET_COLORS[index % DATASET_COLORS.length]}
                radius={[3, 3, 0, 0]}
              />
            ))}
          </BarChart>
        ) : (
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            {axes}
            {tooltip(LINE_CURSOR)}
            {datasets.map((dataset, index) => (
              <Area
                key={dataset.name}
                type="monotone"
                dataKey={dataset.name}
                stroke={DATASET_COLORS[index % DATASET_COLORS.length]}
                fill={DATASET_COLORS[index % DATASET_COLORS.length]}
                fillOpacity={0.25}
              />
            ))}
          </AreaChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
