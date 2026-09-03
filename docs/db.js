/*
 * db.js -- private, browser-local data layer for the research queue,
 * notes, reading list, company research, and report builder (Phase 6).
 *
 * Everything here lives in IndexedDB, in the browser, on this device.
 * Nothing in this file ever sends data anywhere. It is loaded by both
 * index.html (for the "Add to queue" button on intelligence cards) and
 * research.html (the full workspace), so a record added from one page
 * shows up on the other -- both pages are served from the same origin,
 * so they share one IndexedDB database.
 *
 * Public surface, all Promise-based:
 *   ImiDB.addQueueItem(fields)      ImiDB.updateQueueItem(id, patch)
 *   ImiDB.deleteQueueItem(id)       ImiDB.listQueueItems()
 *   ImiDB.addNote(fields)           ImiDB.updateNote(id, patch)
 *   ImiDB.deleteNote(id)            ImiDB.listNotes()
 *   ImiDB.addReadingItem(fields)    ImiDB.updateReadingItem(id, patch)
 *   ImiDB.deleteReadingItem(id)     ImiDB.listReadingItems()
 *   ImiDB.addCompany(fields)        ImiDB.updateCompany(id, patch)
 *   ImiDB.deleteCompany(id)         ImiDB.listCompanies()
 *   ImiDB.addReport(fields)         ImiDB.updateReport(id, patch)
 *   ImiDB.deleteReport(id)          ImiDB.listReports()
 *   ImiDB.exportAllData()           ImiDB.importAllData(data, {mode})
 *
 * Schema is versioned (DB_VERSION). Adding a store bumps DB_VERSION and
 * relies on onupgradeneeded only creating stores that do not exist yet,
 * so existing queue/notes/reading data from an earlier version survives
 * untouched -- verified in test_db.js's "v1 to v2 upgrade" case.
 */

(function (global) {
  "use strict";

  const DB_NAME = "imi-research-db";
  const DB_VERSION = 2;
  const STORES = {
    queue: { name: "queue", keyPath: "id", indexes: ["status", "priority", "dateAdded"] },
    notes: { name: "notes", keyPath: "id", indexes: ["updatedAt"] },
    reading: { name: "reading", keyPath: "id", indexes: ["priority", "status"] },
    companies: { name: "companies", keyPath: "id", indexes: ["country", "sector", "ticker", "updatedAt"] },
    reports: { name: "reports", keyPath: "id", indexes: ["companyId", "updatedAt"] },
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

  // ---- Company research ---------------------------------------------

  function addCompany(fields) {
    const timestamp = nowISO();
    const record = {
      id: uid(),
      company: fields.company || "",
      country: fields.country || "",
      sector: fields.sector || "",
      ticker: fields.ticker || "",
      thesis: fields.thesis || "",
      bullCase: fields.bullCase || "",
      bearCase: fields.bearCase || "",
      risks: fields.risks || "",
      catalysts: fields.catalysts || "",
      metrics: fields.metrics || "",
      notes: fields.notes || "",
      sources: fields.sources || "",
      createdAt: fields.createdAt || timestamp,
      updatedAt: timestamp,
    };
    return storeAdd("companies", record);
  }

  function updateCompany(id, patch) {
    return updateRecord("companies", id, { ...patch, updatedAt: nowISO() });
  }

  function deleteCompany(id) {
    return storeDelete("companies", id);
  }

  function listCompanies() {
    return storeGetAll("companies");
  }

  // ---- Report builder -------------------------------------------------

  function addReport(fields) {
    const timestamp = nowISO();
    const record = {
      id: uid(),
      title: fields.title || "",
      companyId: fields.companyId || "",
      executiveSummary: fields.executiveSummary || "",
      background: fields.background || "",
      financialInformation: fields.financialInformation || "",
      strategy: fields.strategy || "",
      valuation: fields.valuation || "",
      risks: fields.risks || "",
      catalysts: fields.catalysts || "",
      thesis: fields.thesis || "",
      whatCouldProveMeWrong: fields.whatCouldProveMeWrong || "",
      conclusion: fields.conclusion || "",
      sources: fields.sources || "",
      createdAt: fields.createdAt || timestamp,
      updatedAt: timestamp,
    };
    return storeAdd("reports", record);
  }

  function updateReport(id, patch) {
    return updateRecord("reports", id, { ...patch, updatedAt: nowISO() });
  }

  function deleteReport(id) {
    return storeDelete("reports", id);
  }

  function listReports() {
    return storeGetAll("reports");
  }

  // ---- Export / import ------------------------------------------------

  async function exportAllData() {
    const [queue, notes, reading, companies, reports] = await Promise.all([
      listQueueItems(),
      listNotes(),
      listReadingItems(),
      listCompanies(),
      listReports(),
    ]);
    return {
      schema_version: DB_VERSION,
      exported_at: nowISO(),
      queue,
      notes,
      reading,
      companies,
      reports,
    };
  }

  async function importAllData(data, options = {}) {
    const mode = options.mode === "merge" ? "merge" : "replace";
    if (!data || typeof data !== "object") throw new Error("Import data must be an object");
    const queue = Array.isArray(data.queue) ? data.queue : [];
    const notes = Array.isArray(data.notes) ? data.notes : [];
    const reading = Array.isArray(data.reading) ? data.reading : [];
    const companies = Array.isArray(data.companies) ? data.companies : [];
    const reports = Array.isArray(data.reports) ? data.reports : [];

    if (mode === "replace") {
      await Promise.all([
        storeClear("queue"),
        storeClear("notes"),
        storeClear("reading"),
        storeClear("companies"),
        storeClear("reports"),
      ]);
    }
    await Promise.all([
      ...queue.map((record) => storePut("queue", { ...record, id: record.id || uid() })),
      ...notes.map((record) => storePut("notes", { ...record, id: record.id || uid() })),
      ...reading.map((record) => storePut("reading", { ...record, id: record.id || uid() })),
      ...companies.map((record) => storePut("companies", { ...record, id: record.id || uid() })),
      ...reports.map((record) => storePut("reports", { ...record, id: record.id || uid() })),
    ]);
    return {
      queue: queue.length,
      notes: notes.length,
      reading: reading.length,
      companies: companies.length,
      reports: reports.length,
      mode,
    };
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
    addCompany,
    updateCompany,
    deleteCompany,
    listCompanies,
    addReport,
    updateReport,
    deleteReport,
    listReports,
    exportAllData,
    importAllData,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = ImiDB;
  }
  global.ImiDB = ImiDB;
})(typeof window !== "undefined" ? window : globalThis);
