import type { MediaId, MediaItem, MediaSourceResolver, ResolvedMediaSource } from "../../domain/media/types";

export class ObjectUrlMediaSourceResolver implements MediaSourceResolver {
  private readonly urls = new Map<MediaId, ResolvedMediaSource>();

  public register(item: MediaItem, url: string): void {
    const source: ResolvedMediaSource =
      item.source.kind === "local-file" ? { url, mimeType: item.source.mimeType } : { url };
    this.urls.set(item.id, source);
  }

  public async resolve(item: MediaItem): Promise<ResolvedMediaSource> {
    const source = this.urls.get(item.id);
    if (source === undefined) {
      throw new Error(`No runtime URL is registered for ${item.title}`);
    }
    return source;
  }
}
