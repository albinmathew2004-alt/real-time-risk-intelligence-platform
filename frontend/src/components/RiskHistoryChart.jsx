import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const TRIGGER_PATTERNS = [
  { label: "Suspicious Sequence", matchers: ["suspicious sequence", "multiple suspicious", "sequence"] },
  { label: "Clipboard Activity", matchers: ["clipboard", "copy/paste", "copy paste", "paste action"] },
  { label: "Tab Switching", matchers: ["tab switch", "tab switching", "window switch"] },
  { label: "Focus Blur", matchers: ["focus blur", "window blur", "blur event", "focus loss"] },
  { label: "Idle Spike", matchers: ["idle", "inactivity"] },
  { label: "Rapid Answer Burst", matchers: ["rapid answer", "answer burst", "rapid-response"] },
];

function formatChartTime(value, fallbackLabel) {
  if (!value) return fallbackLabel || "Time";
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return fallbackLabel || String(value);
  }
}

function normalizeRiskLevel(score, explicitRiskLevel) {
  if (explicitRiskLevel) return explicitRiskLevel;
  if (score >= 0.7) return "HIGH";
  if (score >= 0.4) return "MEDIUM";
  return "LOW";
}

function describeDominantTrigger(summary, explicitTrigger) {
  if (explicitTrigger) return explicitTrigger;
  const source = String(summary || "").toLowerCase();
  if (!source) return "Risk recalculated from behavioral telemetry";
  const matched = TRIGGER_PATTERNS.find((entry) => entry.matchers.some((matcher) => source.includes(matcher)));
  return matched?.label || "Behavioral risk change detected";
}

function compressHistoryPoints(points) {
  if (points.length <= 5) {
    return points.map((point) => ({ ...point, isKeyPoint: true }));
  }

  const selectedIndexes = new Set([0, points.length - 1]);
  let maxIndex = 0;
  let lastIncludedIndex = 0;

  for (let index = 1; index < points.length; index += 1) {
    if (Number(points[index].score) >= Number(points[maxIndex].score)) {
      maxIndex = index;
    }

    const previous = points[index - 1];
    const current = points[index];
    const previousIncluded = points[lastIncludedIndex];
    const scoreDelta = Math.abs(Number(current.score) - Number(previous.score));
    const scoreDeltaFromIncluded = Math.abs(Number(current.score) - Number(previousIncluded.score));
    const riskChanged = current.riskLevel !== previous.riskLevel;

    if (riskChanged || scoreDelta >= 0.12 || scoreDeltaFromIncluded >= 0.16) {
      selectedIndexes.add(lastIncludedIndex);
      selectedIndexes.add(index);
      lastIncludedIndex = index;
    }
  }

  selectedIndexes.add(maxIndex);

  return [...selectedIndexes]
    .sort((a, b) => a - b)
    .map((index) => ({
      ...points[index],
      isKeyPoint: true,
    }))
    .filter((point, index, array) => {
      if (index === 0 || index === array.length - 1) return true;
      const previous = array[index - 1];
      return !(previous.label === point.label && previous.riskLevel === point.riskLevel && Math.abs(previous.score - point.score) < 0.03);
    });
}

function RiskHistoryTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;

  return (
    <div
      style={{
        background: "#0f172a",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: "14px",
        color: "#f8fafc",
        padding: "12px 14px",
        boxShadow: "0 12px 24px rgba(2, 6, 23, 0.35)",
        maxWidth: "240px",
      }}
    >
      <div style={{ fontSize: "0.82rem", fontWeight: 700, marginBottom: "6px" }}>{point.label}</div>
      <div style={{ fontSize: "0.8rem", color: "#e2e8f0", marginBottom: "4px" }}>
        Risk: <strong style={{ color: "#ffffff" }}>{Number(point.score || 0).toFixed(2)}</strong>
      </div>
      <div style={{ fontSize: "0.76rem", color: "#94a3b8", lineHeight: 1.45 }}>
        Trigger: {point.dominantTrigger || "Behavioral risk change detected"}
      </div>
    </div>
  );
}

export default function RiskHistoryChart({ data, loading }) {
  const rawPoints = data
    .map((item, index) => {
      const score = Number(item.score ?? item.combined_score ?? 0);
      const summary = item.summary || item.reason || "";
      return {
        ...item,
        index,
        x: index,
        label: formatChartTime(item.timestamp, item.label),
        score,
        confidence: Number(item.confidence ?? 0),
        riskLevel: normalizeRiskLevel(score, item.risk_level || item.risk),
        summary,
        dominantTrigger: describeDominantTrigger(summary, item.trigger || item.dominant_trigger || item.event_type),
      };
    })
    .sort((a, b) => {
      const aTime = a.timestamp ? new Date(a.timestamp).getTime() : a.index;
      const bTime = b.timestamp ? new Date(b.timestamp).getTime() : b.index;
      return aTime - bTime;
    })
    .map((item, index) => ({
      ...item,
      x: index,
    }));

  const chartData = compressHistoryPoints(rawPoints);
  const tickLabelMap = Object.fromEntries(chartData.map((item) => [item.x, item.label]));
  const tickIndexes = chartData.length <= 4
    ? chartData.map((item) => item.x)
    : chartData
        .filter((item, index) => index === 0 || index === chartData.length - 1 || index === Math.floor((chartData.length - 1) / 2))
        .map((item) => item.x);
  const pointMarkers = chartData.map((item) => ({
    ...item,
    pointSize: item.isKeyPoint ? 38 : 0,
  }));

  return (
    <section className="investigation-card risk-history-card">
      <div className="report-card-head danger">
        <h3>Risk History</h3>
      </div>

      {loading ? (
        <div className="timeline-empty">Timeline still loading.</div>
      ) : chartData.length === 0 ? (
        <div className="timeline-empty">Waiting for enough risk snapshots to render the timeline.</div>
      ) : (
        <div className="risk-chart-shell">
          <div className="risk-chart-legend">
            <span><i className="high"></i>High (&gt;= 0.70)</span>
            <span><i className="medium"></i>Medium (0.40 - 0.70)</span>
            <span><i className="low"></i>Low (&lt; 0.40)</span>
          </div>
          <div className="risk-chart-frame">
            <ResponsiveContainer width="100%" height={190}>
              <LineChart data={chartData} margin={{ top: 14, right: 8, left: 0, bottom: 6 }}>
                <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                <ReferenceArea y1={0.7} y2={1} fill="rgba(255,77,109,0.12)" strokeOpacity={0} />
                <ReferenceArea y1={0.4} y2={0.7} fill="rgba(255,183,3,0.10)" strokeOpacity={0} />
                <ReferenceArea y1={0} y2={0.4} fill="rgba(34,197,94,0.08)" strokeOpacity={0} />
                <XAxis
                  dataKey="x"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  ticks={tickIndexes}
                  tickFormatter={(value) => tickLabelMap[value] || ""}
                  interval="preserveStartEnd"
                  minTickGap={24}
                  tickMargin={8}
                  tick={{ fill: "#94a3b8", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  padding={{ left: 0, right: 0 }}
                />
                <YAxis
                  domain={[0, 1]}
                  tickCount={4}
                  tick={{ fill: "#94a3b8", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={26}
                />
                <Tooltip
                  cursor={{ stroke: "rgba(79,140,255,0.28)", strokeWidth: 1 }}
                  content={<RiskHistoryTooltip />}
                />
                <Legend wrapperStyle={{ display: "none" }} />
                <Line
                  type="stepAfter"
                  dataKey="score"
                  stroke="#ff7043"
                  strokeWidth={3}
                  dot={false}
                  activeDot={{ r: 6, fill: "#ff7043", stroke: "#ffffff", strokeWidth: 1.5 }}
                  isAnimationActive={false}
                />
                <Scatter data={pointMarkers} dataKey="score" fill="#ff8d5b" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </section>
  );
}
