import type { AudioTransport } from "../../application/audio/audioEngine";
import type { ResolvedMediaSource } from "../../domain/media/types";

export class FakeAudioTransport implements AudioTransport {
  public readonly loadedSources: ResolvedMediaSource[] = [];
  public playCount = 0;
  public pauseCount = 0;
  private positionSeconds = 0;
  private durationSeconds: number | null = 180;

  public async load(source: ResolvedMediaSource): Promise<void> {
    this.loadedSources.push(source);
    this.positionSeconds = 0;
  }

  public async play(): Promise<void> {
    this.playCount += 1;
  }

  public pause(): void {
    this.pauseCount += 1;
  }

  public seek(positionSeconds: number): void {
    this.positionSeconds = positionSeconds;
  }

  public getPositionSeconds(): number {
    return this.positionSeconds;
  }

  public getDurationSeconds(): number | null {
    return this.durationSeconds;
  }

  public setDuration(durationSeconds: number | null): void {
    this.durationSeconds = durationSeconds;
  }
}
