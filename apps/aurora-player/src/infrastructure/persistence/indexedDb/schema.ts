export const AURORA_DB_NAME = "aurora-player";
export const AURORA_DB_VERSION = 1;

export const STORE_NAMES = {
  metadata: "metadata",
  media: "media",
  playlists: "playlists",
  favorites: "favorites",
  history: "history",
  podcastFeeds: "podcastFeeds",
  podcastEpisodes: "podcastEpisodes",
  driveFiles: "driveFiles",
  syncQueue: "syncQueue"
} as const;

export type StoreName = (typeof STORE_NAMES)[keyof typeof STORE_NAMES];

export type SchemaMetadata = {
  readonly key: string;
  readonly value: string;
  readonly updatedAt: string;
};

type StoreDefinition = {
  readonly name: StoreName;
  readonly options: IDBObjectStoreParameters;
  readonly indexes?: readonly {
    readonly name: string;
    readonly keyPath: string | string[];
    readonly options?: IDBIndexParameters;
  }[];
};

export const STORE_DEFINITIONS: readonly StoreDefinition[] = [
  { name: STORE_NAMES.metadata, options: { keyPath: "key" } },
  { name: STORE_NAMES.media, options: { keyPath: "id" }, indexes: [{ name: "kind", keyPath: "kind" }] },
  { name: STORE_NAMES.playlists, options: { keyPath: "id" } },
  { name: STORE_NAMES.favorites, options: { keyPath: "mediaId" } },
  {
    name: STORE_NAMES.history,
    options: { keyPath: "id" },
    indexes: [
      { name: "mediaId", keyPath: "mediaId" },
      { name: "playedAt", keyPath: "playedAt" }
    ]
  },
  {
    name: STORE_NAMES.podcastFeeds,
    options: { keyPath: "id" },
    indexes: [{ name: "feedUrl", keyPath: "feedUrl", options: { unique: true } }]
  },
  {
    name: STORE_NAMES.podcastEpisodes,
    options: { keyPath: "mediaId" },
    indexes: [
      { name: "feedId", keyPath: "feedId" },
      { name: "guid", keyPath: "guid" }
    ]
  },
  { name: STORE_NAMES.driveFiles, options: { keyPath: "id" }, indexes: [{ name: "mimeType", keyPath: "mimeType" }] },
  { name: STORE_NAMES.syncQueue, options: { keyPath: "id" }, indexes: [{ name: "target", keyPath: "target" }] }
];
