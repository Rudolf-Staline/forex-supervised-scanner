import { AURORA_DB_NAME, AURORA_DB_VERSION, STORE_DEFINITIONS, STORE_NAMES, type StoreName } from "./schema";

export type IndexedDbConnection = {
  readonly db: IDBDatabase;
  close(): void;
};

type Migration = {
  readonly version: number;
  migrate(db: IDBDatabase, transaction: IDBTransaction): void;
};

const ensureIndex = (
  store: IDBObjectStore,
  name: string,
  keyPath: string | string[],
  options: IDBIndexParameters | undefined
): void => {
  if (!store.indexNames.contains(name)) {
    store.createIndex(name, keyPath, options);
  }
};

const createV1Schema: Migration = {
  version: 1,
  migrate: (db) => {
    for (const definition of STORE_DEFINITIONS) {
      const store = db.objectStoreNames.contains(definition.name)
        ? undefined
        : db.createObjectStore(definition.name, definition.options);
      if (store !== undefined) {
        for (const index of definition.indexes ?? []) {
          ensureIndex(store, index.name, index.keyPath, index.options);
        }
      }
    }
  }
};

const MIGRATIONS: readonly Migration[] = [createV1Schema];

export const openAuroraDatabase = (databaseName: string = AURORA_DB_NAME): Promise<IndexedDbConnection> =>
  new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, AURORA_DB_VERSION);

    request.onupgradeneeded = (event) => {
      const db = request.result;
      const transaction = request.transaction;
      if (transaction === null) {
        request.onerror = null;
        reject(new Error("IndexedDB upgrade transaction is not available"));
        return;
      }
      const oldVersion = event.oldVersion;
      for (const migration of MIGRATIONS) {
        if (oldVersion < migration.version) {
          migration.migrate(db, transaction);
        }
      }
      const metadata = transaction.objectStore(STORE_NAMES.metadata);
      metadata.put({
        key: "schemaVersion",
        value: String(AURORA_DB_VERSION),
        updatedAt: new Date().toISOString()
      });
    };

    request.onsuccess = () => {
      const db = request.result;
      resolve({
        db,
        close: () => {
          db.close();
        }
      });
    };

    request.onerror = () => {
      reject(request.error ?? new Error("Unable to open Aurora IndexedDB"));
    };

    request.onblocked = () => {
      reject(new Error("Aurora IndexedDB upgrade is blocked by another tab"));
    };
  });

export const requestToPromise = <TValue>(request: IDBRequest<TValue>): Promise<TValue> =>
  new Promise((resolve, reject) => {
    request.onsuccess = () => {
      resolve(request.result);
    };
    request.onerror = () => {
      reject(request.error ?? new Error("IndexedDB request failed"));
    };
  });

export const transactionDone = (transaction: IDBTransaction): Promise<void> =>
  new Promise((resolve, reject) => {
    transaction.oncomplete = () => {
      resolve();
    };
    transaction.onerror = () => {
      reject(transaction.error ?? new Error("IndexedDB transaction failed"));
    };
    transaction.onabort = () => {
      reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
    };
  });

export const withStore = async <TValue>(
  db: IDBDatabase,
  storeName: StoreName,
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => Promise<TValue>
): Promise<TValue> => {
  const transaction = db.transaction(storeName, mode);
  const store = transaction.objectStore(storeName);
  const result = await operation(store);
  await transactionDone(transaction);
  return result;
};

export const withStores = async <TValue>(
  db: IDBDatabase,
  storeNames: readonly StoreName[],
  mode: IDBTransactionMode,
  operation: (transaction: IDBTransaction) => Promise<TValue>
): Promise<TValue> => {
  const transaction = db.transaction([...storeNames], mode);
  const result = await operation(transaction);
  await transactionDone(transaction);
  return result;
};
