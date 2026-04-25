import type { MediaItem } from "../../domain/media/types";

export type RuntimeMediaItem = {
  readonly item: MediaItem;
  readonly objectUrl: string;
};

export type MediaPlayerProps = {
  readonly selected: RuntimeMediaItem | null;
};

export function MediaPlayer({ selected }: MediaPlayerProps): React.ReactElement {
  if (selected === null) {
    return (
      <section className="border border-aurora-border bg-aurora-panel p-5 shadow-aurora" aria-label="Player">
        <p className="text-sm text-aurora-muted">Add local audio or video to begin.</p>
      </section>
    );
  }

  const isVideo = selected.item.kind === "video";
  return (
    <section className="border border-aurora-border bg-aurora-panel p-5 shadow-aurora" aria-label="Player">
      <div className="mb-4">
        <p className="text-sm text-aurora-muted">Now playing</p>
        <h2 className="text-xl font-semibold">{selected.item.title}</h2>
      </div>
      {isVideo ? (
        <video className="aspect-video w-full bg-black" controls src={selected.objectUrl}>
          <track kind="captions" />
        </video>
      ) : (
        <audio className="w-full" controls src={selected.objectUrl} />
      )}
    </section>
  );
}
