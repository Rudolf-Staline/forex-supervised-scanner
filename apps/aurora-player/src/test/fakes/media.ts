import { makeBrand } from "../../domain/common/brand";
import type { MediaItem } from "../../domain/media/types";

export const makeMediaItem = (id: string, title: string = id): MediaItem => ({
  id: makeBrand(id, "MediaId"),
  kind: "audio",
  title,
  source: {
    kind: "local-file",
    fileName: `${title}.mp3`,
    mimeType: "audio/mpeg",
    sizeBytes: 1000,
    lastModifiedMs: 1
  },
  createdAt: "2026-04-15T00:00:00.000Z",
  updatedAt: "2026-04-15T00:00:00.000Z"
});
