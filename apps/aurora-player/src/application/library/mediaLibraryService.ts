import { makeBrand } from "../../domain/common/brand";
import type { MediaId, MediaItem, MediaKind, MediaSource } from "../../domain/media/types";
import type { MediaRepository, SyncOperationRepository } from "../../domain/repositories";
import type { Clock } from "../../shared/time";
import { toIsoDateTime } from "../../shared/time";

export type RegisterMediaInput = {
  readonly title: string;
  readonly kind: MediaKind;
  readonly source: MediaSource;
  readonly artist?: string;
  readonly album?: string;
  readonly durationSeconds?: number;
  readonly artworkUrl?: string;
};

export class MediaLibraryService {
  public constructor(
    private readonly mediaRepository: MediaRepository,
    private readonly syncOperations: SyncOperationRepository,
    private readonly clock: Clock
  ) {}

  public async register(input: RegisterMediaInput): Promise<MediaItem> {
    const now = toIsoDateTime(this.clock.now());
    const item = this.toMediaItem(input, now);
    await this.mediaRepository.upsert(item);
    await this.syncOperations.enqueue({
      id: makeBrand(`sync_${crypto.randomUUID()}`, "SyncOperationId"),
      target: "user-profile",
      kind: "media-upserted",
      entityId: item.id,
      createdAt: now,
      retryCount: 0,
      payload: { mediaId: item.id }
    });
    return item;
  }

  public async list(): Promise<readonly MediaItem[]> {
    return this.mediaRepository.list();
  }

  private toMediaItem(input: RegisterMediaInput, now: string): MediaItem {
    const title = input.title.trim();
    if (title.length === 0) {
      throw new Error("Media title cannot be empty");
    }
    const idSeed = `${input.source.kind}:${title}:${now}`;
    const item: MediaItem = {
      id: makeBrand(this.stableId(idSeed), "MediaId"),
      title,
      kind: input.kind,
      source: input.source,
      createdAt: now,
      updatedAt: now
    };
    return {
      ...item,
      ...(input.artist === undefined ? {} : { artist: input.artist }),
      ...(input.album === undefined ? {} : { album: input.album }),
      ...(input.durationSeconds === undefined ? {} : { durationSeconds: input.durationSeconds }),
      ...(input.artworkUrl === undefined ? {} : { artworkUrl: input.artworkUrl })
    };
  }

  private stableId(seed: string): MediaId {
    const encoded = Array.from(seed)
      .map((char) => char.charCodeAt(0).toString(16).padStart(2, "0"))
      .join("")
      .slice(0, 48);
    return makeBrand(`media_${encoded}`, "MediaId");
  }
}
