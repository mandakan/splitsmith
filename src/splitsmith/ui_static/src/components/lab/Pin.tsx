export function Pin({
  time,
  duration,
  color,
  label,
  top,
}: {
  time: number;
  duration: number;
  color: string;
  label: string;
  top?: boolean;
}) {
  const left = duration > 0 ? `${(time / duration) * 100}%` : "0%";
  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left,
        top: top ? 4 : undefined,
        bottom: top ? undefined : 4,
        width: 2,
        height: 32,
        background: color,
        transform: "translateX(-1px)",
      }}
      title={`${label} @ ${time.toFixed(3)}s`}
    />
  );
}
