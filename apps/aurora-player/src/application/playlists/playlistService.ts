import type { PlaylistRepository, SyncOperationRepository } from "../../domain/repositories";
import type { MediaId } from "../../domain/media/types";
import type { Playlist, PlaylistId } from "../../domain/playlists/types";
import type { Clock } from "../../shared/time";
import { toIsoDateTime } from "../../shared/time";
import { makeBrand } from "../../domain/common/brand";

export class PlaylistService {
  public constructor(
    private readonly playlists: PlaylistRepository,
    private readonly syncOperations: SyncOperationRepository,
    private readonly clock: Clock
  ) {}

  public async create(name: string, mediaIds: readonly MediaId[] = []): Promise<Playlist> {
    const now = toIsoDateTime(this.clock.now());
    const playlist: Playlist = {
      id: makeBrand(`playlist_${crypto.randomUUID()}`, "PlaylistId"),
      name: this.requireName(name),
      mediaIds: [...mediaIds],
      createdAt: now,
      updatedAt: now
    };
    await this.playlists.upsert(playlist);
    await this.syncOperations.enqueue({
      id: makeBrand(`sync_${crypto.randomUUID()}`, "SyncOperationId"),
      target: "user-profile",
      kind: "playlist-upserted",
      entityId: playlist.id,
      createdAt: now,
      retryCount: 0,
      payload: { playlistId: playlist.id }
    });
    return playlist;
  }

  public async addMedia(playlistId: PlaylistId, mediaId: MediaId): Promise<Playlist> {
    const playlist = await this.playlists.getById(playlistId);
    if (playlist === null) {
      throw new Error(`Playlist not found: ${playlistId}`);
    }
    if (playlist.mediaIds.includes(mediaId)) {
      return playlist;
    }
    const updated: Playlist = {
      ...playlist,
      mediaIds: [...playlist.mediaIds, mediaId],
      updatedAt: toIsoDateTime(this.clock.now())
    };
    await this.playlists.upsert(updated);
    return updated;
  }

  private requireName(name: string): string {
    const trimmed = name.trim();
    if (trimmed.length === 0) {
      throw new Error("Playlist name cannot be empty");
    }
    return trimmed;
  }
}
