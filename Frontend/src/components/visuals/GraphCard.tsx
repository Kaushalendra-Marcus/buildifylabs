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

const DATASET_COLORS = [
  'var(--accent)',
  'var(--success)',
  'var(--warning)',
  'var(--danger)',
];

const TOOLTIP_STYLE = {
  background: 'var(--surface-raised)',
  border: '1px solid color-mix(in srgb, var(--text-muted) 40%, transparent)',
  borderRadius: 8,
  fontSize: 12,
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

export function GraphCard({ props }: { props: GraphProps }) {
  const { chart_type, labels, datasets } = props;
  const data = buildRows(props);

  if (chart_type === 'pie') {
    const pieData = labels.map((label, index) => ({
      name: label,
      value: datasets[0]?.values[index] ?? 0,
    }));
    return (
      <div className="visual-graph" role="img" aria-label={`${chart_type} chart`}>
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={pieData}
              dataKey="value"
              nameKey="name"
              outerRadius={80}
              paddingAngle={2}
            >
              {pieData.map((slice, index) => (
                <Cell
                  key={slice.name}
                  fill={DATASET_COLORS[index % DATASET_COLORS.length]}
                />
              ))}
            </Pie>
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
          </PieChart>
        </ResponsiveContainer>
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
      <Tooltip contentStyle={TOOLTIP_STYLE} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  );

  return (
    <div className="visual-graph" role="img" aria-label={`${chart_type} chart`}>
      <ResponsiveContainer width="100%" height={220}>
        {chart_type === 'line' ? (
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            {axes}
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
