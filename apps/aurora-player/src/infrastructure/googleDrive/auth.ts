import type { Clock } from "../../shared/time";
import type { DriveAccessToken } from "./types";

export interface DriveTokenStore {
  load(): Promise<DriveAccessToken | null>;
  save(token: DriveAccessToken): Promise<void>;
  clear(): Promise<void>;
}

export interface DriveTokenRefresher {
  refresh(): Promise<DriveAccessToken>;
}

const REFRESH_WINDOW_MS = 60_000;

export class GoogleDriveAuthService {
  public constructor(
    private readonly tokenStore: DriveTokenStore,
    private readonly refresher: DriveTokenRefresher,
    private readonly clock: Clock
  ) {}

  public async getAccessToken(): Promise<string> {
    const token = await this.tokenStore.load();
    if (token !== null && token.expiresAtMs - this.clock.now().getTime() > REFRESH_WINDOW_MS) {
      return token.accessToken;
    }
    const refreshed = await this.refreshAccessToken();
    return refreshed.accessToken;
  }

  public async refreshAccessToken(): Promise<DriveAccessToken> {
    const token = await this.refresher.refresh();
    if (token.accessToken.trim().length === 0) {
      throw new Error("Google Drive token refresher returned an empty token");
    }
    await this.tokenStore.save(token);
    return token;
  }

  public async clear(): Promise<void> {
    await this.tokenStore.clear();
  }
}
