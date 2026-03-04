/**
 * SessionStorage keys and IndexedDB cache for batch voice clone.
 * Persists batchId and lastKnownStatus so the UI can resume after refresh;
 * optionally caches ZIP blob for offline download.
 */

const SESSION_BATCH_ID = 'voicebox_batch_clone_id';
const SESSION_BATCH_STATUS = 'voicebox_batch_clone_status';
const IDB_NAME = 'voicebox_batch_clone';
const IDB_STORE = 'batch_zips';

export function getStoredBatchId(): string | null {
  if (typeof sessionStorage === 'undefined') return null;
  return sessionStorage.getItem(SESSION_BATCH_ID);
}

export function setStoredBatchId(batchId: string): void {
  sessionStorage.setItem(SESSION_BATCH_ID, batchId);
}

export function clearStoredBatchId(): void {
  sessionStorage.removeItem(SESSION_BATCH_ID);
}

export function getStoredBatchStatus(): string | null {
  if (typeof sessionStorage === 'undefined') return null;
  return sessionStorage.getItem(SESSION_BATCH_STATUS);
}

export function setStoredBatchStatus(status: string): void {
  sessionStorage.setItem(SESSION_BATCH_STATUS, status);
}

export function clearStoredBatchStatus(): void {
  sessionStorage.removeItem(SESSION_BATCH_STATUS);
}

export function clearStoredBatch(): void {
  clearStoredBatchId();
  clearStoredBatchStatus();
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
    req.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE, { keyPath: 'batchId' });
      }
    };
  });
}

export function cacheBatchZip(batchId: string, blob: Blob): Promise<void> {
  return openDb().then((db) => {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      const store = tx.objectStore(IDB_STORE);
      const req = store.put({ batchId, blob });
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve();
      tx.oncomplete = () => db.close();
    });
  });
}

export function getCachedBatchZip(batchId: string): Promise<Blob | null> {
  return openDb().then((db) => {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readonly');
      const store = tx.objectStore(IDB_STORE);
      const req = store.get(batchId);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const row = req.result as { batchId: string; blob: Blob } | undefined;
        resolve(row?.blob ?? null);
      };
      tx.oncomplete = () => db.close();
    });
  });
}

export function clearCachedBatchZip(batchId: string): Promise<void> {
  return openDb().then((db) => {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      const store = tx.objectStore(IDB_STORE);
      const req = store.delete(batchId);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve();
      tx.oncomplete = () => db.close();
    });
  });
}
