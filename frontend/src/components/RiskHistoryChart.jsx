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

export default function RiskHistoryChart({ data, loading }) {
  const rawPoints = data
    .map((item, index) => {
      const score = Number(item.score ?? item.combined_score ?? 0);
      return {
        ...item,
        index,
        x: index,
        label: formatChartTime(item.timestamp, item.label),
        score,
        confidence: Number(item.confidence ?? 0),
        riskLevel: normalizeRiskLevel(score, item.risk_level || item.risk),
        summary: item.summary || item.reason || "",
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
        <div className="timeline-empty">Loading risk movement...</div>
      ) : chartData.length === 0 ? (
        <div className="timeline-empty">No risk history points are available for this attempt yet.</div>
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
                  contentStyle={{
                    background: "#0f172a",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    color: "#f8fafc",
                  }}
                  formatter={(value, name) => [Number(value).toFixed(2), name === "score" ? "Risk Score" : "Confidence"]}
                  labelFormatter={(label, payload) => {
                    const point = payload?.[0]?.payload;
                    const level = point?.riskLevel ? ` | ${point.riskLevel}` : "";
                    return `Time ${point?.label || label}${level}`;
                  }}
                />
                <Legend wrapperStyle={{ display: "none" }} />
                <Line
                  type="monotone"
                  dataKey="score"
                  stroke="#ff7043"
                  strokeWidth={3}
                  dot={false}
                  activeDot={{ r: 6, fill: "#ff7043" }}
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
