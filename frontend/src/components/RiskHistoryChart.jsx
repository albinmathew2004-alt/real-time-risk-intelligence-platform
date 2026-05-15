import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export default function RiskHistoryChart({ data, loading }) {
  const chartData = data.map((item) => ({
    ...item,
    label: item.label,
    score: Number(item.score ?? item.combined_score ?? 0),
    confidence: Number(item.confidence ?? 0),
    riskLevel: item.risk_level || item.risk || "LOW",
    summary: item.summary || item.reason || "",
  }));

  return (
    <section className="investigation-card risk-history-card">
      <div className="report-card-head danger">
        <h3>Risk History</h3>
      </div>

      {loading ? (
        <div className="timeline-empty">Loading risk movement…</div>
      ) : chartData.length === 0 ? (
        <div className="timeline-empty">No risk history points are available for this attempt yet.</div>
      ) : (
        <div className="risk-chart-shell">
          <div className="risk-chart-legend">
            <span><i className="high"></i>High (0.70+)</span>
            <span><i className="medium"></i>Medium (0.40 - 0.70)</span>
            <span><i className="low"></i>Low (&lt; 0.40)</span>
          </div>
          <div className="risk-chart-frame">
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={chartData} margin={{ top: 18, right: 20, left: 4, bottom: 10 }}>
                <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                <ReferenceArea y1={0.7} y2={1} fill="rgba(255,77,109,0.12)" strokeOpacity={0} />
                <ReferenceArea y1={0.4} y2={0.7} fill="rgba(255,183,3,0.10)" strokeOpacity={0} />
                <ReferenceArea y1={0} y2={0.4} fill="rgba(34,197,94,0.08)" strokeOpacity={0} />
                <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis
                  domain={[0, 1]}
                  tickCount={5}
                  tick={{ fill: "#94a3b8", fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
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
                    const level = point?.riskLevel ? ` • ${point.riskLevel}` : "";
                    return `Time ${label}${level}`;
                  }}
                />
                <Legend wrapperStyle={{ display: "none" }} />
                <Line
                  type="monotone"
                  dataKey="score"
                  stroke="#ff7043"
                  strokeWidth={3}
                  dot={{ r: 4, strokeWidth: 2, fill: "#ff7043", stroke: "#1f2937" }}
                  activeDot={{ r: 6, fill: "#ff7043" }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </section>
  );
}
