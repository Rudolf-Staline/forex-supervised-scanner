import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { MediaPlayer, type RuntimeMediaItem } from "./components/player/MediaPlayer";
import { AuroraShell } from "./components/layout/AuroraShell";
import { makeBrand } from "./domain/common/brand";
import type { MediaItem, MediaKind } from "./domain/media/types";
import { DrivePage } from "./features/drive/DrivePage";
import { LibraryPage } from "./features/library/LibraryPage";
import { PodcastPage } from "./features/podcasts/PodcastPage";
import { toIsoDateTime } from "./shared/time";

const isSupportedMedia = (file: File): boolean => file.type.startsWith("audio/") || file.type.startsWith("video/");

const mediaKindFor = (file: File): MediaKind => (file.type.startsWith("video/") ? "video" : "audio");

const mediaIdFor = (file: File): MediaItem["id"] =>
  makeBrand(`media_${file.name}_${file.size}_${file.lastModified}`, "MediaId");

const createMediaItem = (file: File): MediaItem => {
  const now = toIsoDateTime(new Date());
  return {
    id: mediaIdFor(file),
    kind: mediaKindFor(file),
    title: file.name,
    source: {
      kind: "local-file",
      fileName: file.name,
      mimeType: file.type || "application/octet-stream",
      sizeBytes: file.size,
      lastModifiedMs: file.lastModified
    },
    createdAt: now,
    updatedAt: now
  };
};

export function App(): React.ReactElement {
  const [items, setItems] = useState<readonly RuntimeMediaItem[]>([]);
  const [selected, setSelected] = useState<RuntimeMediaItem | null>(null);

  const addFiles = useCallback((files: readonly File[]) => {
    const nextItems = files.filter(isSupportedMedia).map((file) => ({
      item: createMediaItem(file),
      objectUrl: URL.createObjectURL(file)
    }));
    setItems((current) => [...current, ...nextItems]);
    setSelected((current) => current ?? nextItems[0] ?? null);
  }, []);

  useEffect(
    () => () => {
      for (const entry of items) {
        URL.revokeObjectURL(entry.objectUrl);
      }
    },
    [items]
  );

  const libraryRoute = useMemo(
    () => (
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_480px]">
        <LibraryPage
          items={items}
          selectedId={selected?.item.id ?? null}
          onFilesSelected={addFiles}
          onSelect={setSelected}
        />
        <MediaPlayer selected={selected} />
      </div>
    ),
    [addFiles, items, selected]
  );

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AuroraShell />}>
          <Route index element={libraryRoute} />
          <Route path="/drive" element={<DrivePage />} />
          <Route path="/podcasts" element={<PodcastPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
