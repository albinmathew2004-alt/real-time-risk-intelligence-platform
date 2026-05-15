export default function TechnicalDetails({ items }) {
  return (
    <section className="investigation-card technical-details-card">
      <div className="report-card-head">
        <h3>Technical Details</h3>
      </div>
      <div className="technical-details-grid">
        {items.map((item) => (
          <div className="technical-details-item" key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
