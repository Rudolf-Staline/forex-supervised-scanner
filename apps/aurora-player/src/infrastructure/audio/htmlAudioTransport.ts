import type { AudioTransport } from "../../application/audio/audioEngine";
import type { ResolvedMediaSource } from "../../domain/media/types";

export class HtmlAudioTransport implements AudioTransport {
  public constructor(private readonly audio: HTMLAudioElement) {}

  public async load(source: ResolvedMediaSource): Promise<void> {
    this.audio.src = source.url;
    this.audio.preload = "metadata";
    this.audio.load();
    await this.waitForMetadata();
  }

  public async play(): Promise<void> {
    await this.audio.play();
  }

  public pause(): void {
    this.audio.pause();
  }

  public seek(positionSeconds: number): void {
    this.audio.currentTime = positionSeconds;
  }

  public getPositionSeconds(): number {
    return Number.isFinite(this.audio.currentTime) ? this.audio.currentTime : 0;
  }

  public getDurationSeconds(): number | null {
    return Number.isFinite(this.audio.duration) ? this.audio.duration : null;
  }

  private waitForMetadata(): Promise<void> {
    if (this.audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const cleanup = (): void => {
        this.audio.removeEventListener("loadedmetadata", handleLoaded);
        this.audio.removeEventListener("error", handleError);
      };
      const handleLoaded = (): void => {
        cleanup();
        resolve();
      };
      const handleError = (): void => {
        cleanup();
        reject(new Error("Audio metadata could not be loaded"));
      };
      this.audio.addEventListener("loadedmetadata", handleLoaded, { once: true });
      this.audio.addEventListener("error", handleError, { once: true });
    });
  }
}
