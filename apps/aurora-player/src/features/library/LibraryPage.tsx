import { VirtualizedList } from "../../components/virtualized/VirtualizedList";
import type { RuntimeMediaItem } from "../../components/player/MediaPlayer";

export type LibraryPageProps = {
  readonly items: readonly RuntimeMediaItem[];
  readonly selectedId: string | null;
  readonly onFilesSelected: (files: readonly File[]) => void;
  readonly onSelect: (item: RuntimeMediaItem) => void;
};

export function LibraryPage({
  items,
  selectedId,
  onFilesSelected,
  onSelect
}: LibraryPageProps): React.ReactElement {
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold">Library</h2>
            <p className="text-sm text-aurora-muted">Local audio and video stay on this device.</p>
          </div>
          <label
            className={[
              "cursor-pointer rounded-md bg-aurora-green px-4 py-2 text-sm font-semibold text-aurora-night",
              "outline-none focus-within:ring-2 focus-within:ring-aurora-cyan"
            ].join(" ")}
          >
            Add files
            <input
              accept="audio/*,video/*"
              className="sr-only"
              multiple
              type="file"
              onChange={(event) => {
                onFilesSelected(Array.from(event.currentTarget.files ?? []));
                event.currentTarget.value = "";
              }}
            />
          </label>
        </div>

        {items.length === 0 ? (
          <div className="border border-aurora-border bg-aurora-panel p-6 text-aurora-muted">
            Your local queue is empty.
          </div>
        ) : (
          <VirtualizedList
            ariaLabel="Local media library"
            getKey={(entry) => entry.item.id}
            height={520}
            itemHeight={76}
            items={items}
            renderItem={(entry) => {
              const isSelected = selectedId === entry.item.id;
              return (
                <button
                  className={[
                    "flex h-full w-full items-center justify-between border-b border-aurora-border px-4",
                    "text-left outline-none transition",
                    "focus-visible:ring-2 focus-visible:ring-aurora-cyan",
                    isSelected ? "bg-aurora-cyan text-aurora-night" : "hover:bg-black"
                  ].join(" ")}
                  type="button"
                  onClick={() => {
                    onSelect(entry);
                  }}
                >
                  <span>
                    <span className="block font-medium">{entry.item.title}</span>
                    <span className={isSelected ? "text-aurora-night" : "text-aurora-muted"}>
                      {entry.item.kind}
                    </span>
                  </span>
                  <span className="text-sm">
                    {entry.item.source.kind === "local-file" ? entry.item.source.mimeType : ""}
                  </span>
                </button>
              );
            }}
          />
        )}
      </div>
      <aside className="border border-aurora-border bg-aurora-panel p-5">
        <h3 className="mb-2 text-lg font-semibold">Session</h3>
        <p className="text-sm text-aurora-muted">
          Imported files are available while this tab is open. Durable metadata is handled by the IndexedDB repository
          layer.
        </p>
      </aside>
    </section>
  );
}
