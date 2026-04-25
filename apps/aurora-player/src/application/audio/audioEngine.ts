import { PlaybackQueue } from "./playbackQueue";
import type { MediaItem, MediaSourceResolver, ResolvedMediaSource } from "../../domain/media/types";
import type { PlaybackSnapshot, PlaybackStatus } from "../../domain/playback/types";

export interface AudioTransport {
  load(source: ResolvedMediaSource): Promise<void>;
  play(): Promise<void>;
  pause(): void;
  seek(positionSeconds: number): void;
  getPositionSeconds(): number;
  getDurationSeconds(): number | null;
}

export type AudioEngineListener = (snapshot: PlaybackSnapshot) => void;

export class AudioEngineService {
  private status: PlaybackStatus = "idle";
  private errorMessage: string | null = null;
  private readonly listeners = new Set<AudioEngineListener>();

  public constructor(
    private readonly transport: AudioTransport,
    private readonly resolver: MediaSourceResolver,
    private readonly queue: PlaybackQueue = new PlaybackQueue()
  ) {}

  public subscribe(listener: AudioEngineListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  public snapshot(): PlaybackSnapshot {
    return {
      queue: this.queue.snapshot(),
      status: this.status,
      positionSeconds: this.transport.getPositionSeconds(),
      durationSeconds: this.transport.getDurationSeconds(),
      errorMessage: this.errorMessage
    };
  }

  public async loadQueue(items: readonly MediaItem[], startId?: MediaItem["id"]): Promise<void> {
    this.queue.load(items, startId);
    await this.loadCurrent("paused");
  }

  public async play(): Promise<void> {
    const current = this.queue.current();
    if (current === null) {
      this.status = "idle";
      this.emit();
      return;
    }
    await this.loadAndPlay(current);
  }

  public pause(): void {
    this.transport.pause();
    this.status = "paused";
    this.emit();
  }

  public seek(positionSeconds: number): void {
    if (!Number.isFinite(positionSeconds) || positionSeconds < 0) {
      throw new Error("Seek position must be a positive finite number");
    }
    this.transport.seek(positionSeconds);
    this.emit();
  }

  public async next(): Promise<void> {
    const next = this.queue.next();
    if (next === null) {
      this.status = "ended";
      this.emit();
      return;
    }
    await this.loadAndPlay(next);
  }

  public async previous(): Promise<void> {
    const previous = this.queue.previous();
    if (previous === null) {
      this.status = "idle";
      this.emit();
      return;
    }
    await this.loadAndPlay(previous);
  }

  public setRepeat(mode: PlaybackSnapshot["queue"]["repeat"]): void {
    this.queue.setRepeat(mode);
    this.emit();
  }

  public setShuffle(enabled: boolean): void {
    this.queue.setShuffle(enabled);
    this.emit();
  }

  public async handleTrackEnded(): Promise<void> {
    const next = this.queue.handleTrackEnded();
    if (next === null) {
      this.status = "ended";
      this.emit();
      return;
    }
    await this.loadAndPlay(next);
  }

  private async loadCurrent(nextStatus: PlaybackStatus): Promise<void> {
    const current = this.queue.current();
    if (current === null) {
      this.status = "idle";
      this.emit();
      return;
    }
    await this.load(current, nextStatus);
  }

  private async loadAndPlay(item: MediaItem): Promise<void> {
    await this.load(item, "loading");
    try {
      await this.transport.play();
      this.status = "playing";
      this.errorMessage = null;
      this.emit();
    } catch (error) {
      this.fail(error);
      throw error;
    }
  }

  private async load(item: MediaItem, nextStatus: PlaybackStatus): Promise<void> {
    this.status = "loading";
    this.errorMessage = null;
    this.emit();
    try {
      const source = await this.resolver.resolve(item);
      await this.transport.load(source);
      this.status = nextStatus;
      this.emit();
    } catch (error) {
      this.fail(error);
      throw error;
    }
  }

  private fail(error: unknown): void {
    this.status = "error";
    this.errorMessage = error instanceof Error ? error.message : "Unknown audio error";
    this.emit();
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) {
      listener(snapshot);
    }
  }
}
