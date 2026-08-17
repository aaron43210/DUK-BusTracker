// src/components/Legend.jsx
const LEGEND_ITEMS = [
  { cls: 'legend-line legend-line--route', label: 'Route' },
  { cls: 'legend-line legend-line--trail', label: 'Live trail' },
  { cls: 'legend-dot legend-dot--visited', label: 'Visited stop' },
  { cls: 'legend-dot legend-dot--next',    label: 'Next stop' },
  { cls: 'legend-bus',                     label: 'Bus position' },
];

export default function Legend() {
  return (
    <div className="legend">
      {LEGEND_ITEMS.map(({ cls, label }) => (
        <div className="legend-row" key={label}>
          <span className={cls} />
          {label}
        </div>
      ))}
    </div>
  );
}
