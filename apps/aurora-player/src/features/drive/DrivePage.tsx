export function DrivePage(): React.ReactElement {
  return (
    <section className="max-w-3xl">
      <h2 className="text-2xl font-semibold">Google Drive</h2>
      <p className="mt-3 text-aurora-muted">
        Drive indexing is handled by an isolated auth, HTTP, repository and sync layer. Add a concrete OAuth adapter
        before enabling account connection in the UI.
      </p>
    </section>
  );
}
