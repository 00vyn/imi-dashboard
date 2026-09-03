/*
 * db.js -- private, browser-local data layer for the research queue,
 * notes, and reading list (Phase 6).
 *
 * Everything here lives in IndexedDB, in the browser, on this device.
 * Nothing in this file ever sends data anywhere. It is loaded by both
 * index.html (for the "Add to queue" button on intelligence cards) and
 * research.html (the full queue/notes/reading-list workspace), so a
 * record added from one page shows up on the other -- both pages are
 * served from the same origin, so they share one IndexedDB database.
 *
 * Public surface, all Promise-based:
 *   ImiDB.addQueueItem(fields)      ImiDB.updateQueueItem(id, patch)
 *   ImiDB.deleteQueueItem(id)       ImiDB.listQueueItems()
 *   ImiDB.addNote(fields)           ImiDB.updateNote(id, patch)
 *   ImiDB.deleteNote(id)            ImiDB.listNotes()
 *   ImiDB.addReadingItem(fields)    ImiDB.updateReadingItem(id, patch)
 *   ImiDB.deleteReadingItem(id)     ImiDB.listReadingItems()
 *   ImiDB.exportAllData()           ImiDB.importAllData(data, {mode})
 *
 * Schema is versioned (DB_VERSION) so Phase 6's later slice (company
 * research, report builder) can add stores with a version bump without
 * touching what is here.
 */

(function (global) {
  "use strict";

  const DB_NAME = "imi-research-db";
  const DB_VERSION = 1;
  const STORES = {
    queue: { name: "queue", keyPath: "id", indexes: ["status", "priority", "dateAdded"] },
    notes: { name: "notes", keyPath: "id", indexes: ["updatedAt"] },
    reading: { name: "reading", keyPath: "id", indexes: ["priority", "status"] },
  };

  let dbPromise = null;

  function uid() {
    if (global.crypto && typeof global.crypto.randomUUID === "function") {
      return global.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function nowISO() {
    return new Date().toISOString();
  }

  function openDB() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = global.indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        for (const store of Object.values(STORES)) {
          if (!db.objectStoreNames.contains(store.name)) {
            const os = db.createObjectStore(store.name, { keyPath: store.keyPath });
            for (const indexName of store.indexes) {
              os.createIndex(indexName, indexName, { unique: false });
            }
          }
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return dbPromise;
  }

  function withStore(storeName, mode, work) {
    return openDB().then(
      (db) =>
        new Promise((resolve, reject) => {
          const tx = db.transaction(storeName, mode);
          const store = tx.objectStore(storeName);
          let result;
          Promise.resolve(work(store))
            .then((value) => {
              result = value;
            })
            .catch(reject);
          tx.oncomplete = () => resolve(result);
          tx.onerror = () => reject(tx.error);
          tx.onabort = () => reject(tx.error || new Error(`${storeName} transaction aborted`));
        })
    );
  }

  function requestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function storeAdd(storeName, record) {
    return withStore(storeName, "readwrite", (store) => requestToPromise(store.add(record))).then(() => record);
  }

  function storePut(storeName, record) {
    return withStore(storeName, "readwrite", (store) => requestToPromise(store.put(record))).then(() => record);
  }

  function storeDelete(storeName, id) {
    return withStore(storeName, "readwrite", (store) => requestToPromise(store.delete(id)));
  }

  function storeGetAll(storeName) {
    return withStore(storeName, "readonly", (store) => requestToPromise(store.getAll()));
  }

  function storeGet(storeName, id) {
    return withStore(storeName, "readonly", (store) => requestToPromise(store.get(id)));
  }

  function storeClear(storeName) {
    return withStore(storeName, "readwrite", (store) => requestToPromise(store.clear()));
  }

  async function updateRecord(storeName, id, patch) {
    const existing = await storeGet(storeName, id);
    if (!existing) throw new Error(`${storeName} record not found: ${id}`);
    const updated = { ...existing, ...patch, id };
    return storePut(storeName, updated);
  }

  // ---- Research queue -------------------------------------------------

  function addQueueItem(fields) {
    const record = {
      id: uid(),
      title: fields.title || "",
      description: fields.description || "",
      category: fields.category || "",
      source: fields.source || "",
      dateAdded: fields.dateAdded || nowISO(),
      priority: fields.priority || "Medium",
      status: fields.status || "todo",
      notes: fields.notes || "",
    };
    return storeAdd("queue", record);
  }

  function updateQueueItem(id, patch) {
    return updateRecord("queue", id, patch);
  }

  function deleteQueueItem(id) {
    return storeDelete("queue", id);
  }

  function listQueueItems() {
    return storeGetAll("queue");
  }

  // ---- Notes ------------------------------------------------------------

  function addNote(fields) {
    const timestamp = nowISO();
    const record = {
      id: uid(),
      title: fields.title || "",
      body: fields.body || "",
      tags: Array.isArray(fields.tags) ? fields.tags : [],
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    return storeAdd("notes", record);
  }

  function updateNote(id, patch) {
    return updateRecord("notes", id, { ...patch, updatedAt: nowISO() });
  }

  function deleteNote(id) {
    return storeDelete("notes", id);
  }

  function listNotes() {
    return storeGetAll("notes");
  }

  // ---- Reading list -------------------------------------------------------

  function addReadingItem(fields) {
    const record = {
      id: uid(),
      title: fields.title || "",
      author: fields.author || "",
      importance: fields.importance || "Medium",
      difficulty: fields.difficulty || "Medium",
      expectedLearning: fields.expectedLearning || "",
      priority: fields.priority || "Medium",
      status: fields.status || "unread",
      addedAt: fields.addedAt || nowISO(),
    };
    return storeAdd("reading", record);
  }

  function updateReadingItem(id, patch) {
    return updateRecord("reading", id, patch);
  }

  function deleteReadingItem(id) {
    return storeDelete("reading", id);
  }

  function listReadingItems() {
    return storeGetAll("reading");
  }

  // ---- Export / import ------------------------------------------------

  async function exportAllData() {
    const [queue, notes, reading] = await Promise.all([
      listQueueItems(),
      listNotes(),
      listReadingItems(),
    ]);
    return {
      schema_version: DB_VERSION,
      exported_at: nowISO(),
      queue,
      notes,
      reading,
    };
  }

  async function importAllData(data, options = {}) {
    const mode = options.mode === "merge" ? "merge" : "replace";
    if (!data || typeof data !== "object") throw new Error("Import data must be an object");
    const queue = Array.isArray(data.queue) ? data.queue : [];
    const notes = Array.isArray(data.notes) ? data.notes : [];
    const reading = Array.isArray(data.reading) ? data.reading : [];

    if (mode === "replace") {
      await Promise.all([storeClear("queue"), storeClear("notes"), storeClear("reading")]);
    }
    await Promise.all([
      ...queue.map((record) => storePut("queue", { ...record, id: record.id || uid() })),
      ...notes.map((record) => storePut("notes", { ...record, id: record.id || uid() })),
      ...reading.map((record) => storePut("reading", { ...record, id: record.id || uid() })),
    ]);
    return { queue: queue.length, notes: notes.length, reading: reading.length, mode };
  }

  const ImiDB = {
    addQueueItem,
    updateQueueItem,
    deleteQueueItem,
    listQueueItems,
    addNote,
    updateNote,
    deleteNote,
    listNotes,
    addReadingItem,
    updateReadingItem,
    deleteReadingItem,
    listReadingItems,
    exportAllData,
    importAllData,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = ImiDB;
  }
  global.ImiDB = ImiDB;
})(typeof window !== "undefined" ? window : globalThis);
