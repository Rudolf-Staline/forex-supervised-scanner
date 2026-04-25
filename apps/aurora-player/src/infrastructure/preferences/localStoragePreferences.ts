export type AuroraThemePreference = "dark";

export type AuroraPreferences = {
  readonly theme: AuroraThemePreference;
  readonly volume: number;
  readonly lastRoute: string;
};

const DEFAULT_PREFERENCES: AuroraPreferences = {
  theme: "dark",
  volume: 0.8,
  lastRoute: "/"
};

const STORAGE_KEY = "aurora.preferences.v1";

const isPreferenceRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export class LocalStoragePreferences {
  public constructor(private readonly storage: Storage = localStorage) {}

  public load(): AuroraPreferences {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (raw === null) {
      return DEFAULT_PREFERENCES;
    }
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!isPreferenceRecord(parsed)) {
        return DEFAULT_PREFERENCES;
      }
      return {
        theme: "dark",
        volume: typeof parsed.volume === "number" ? this.clampVolume(parsed.volume) : DEFAULT_PREFERENCES.volume,
        lastRoute: typeof parsed.lastRoute === "string" ? parsed.lastRoute : DEFAULT_PREFERENCES.lastRoute
      };
    } catch {
      return DEFAULT_PREFERENCES;
    }
  }

  public save(preferences: AuroraPreferences): void {
    this.storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        theme: "dark",
        volume: this.clampVolume(preferences.volume),
        lastRoute: preferences.lastRoute
      })
    );
  }

  private clampVolume(volume: number): number {
    if (!Number.isFinite(volume)) {
      return DEFAULT_PREFERENCES.volume;
    }
    return Math.min(1, Math.max(0, volume));
  }
}
