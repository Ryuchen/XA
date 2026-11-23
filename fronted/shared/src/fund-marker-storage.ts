const databaseName = "xa-fund-markers"
const markerStore = "pending"
let connection: IDBDatabase | null = null
let opening: Promise<IDBDatabase> | null = null

function openFundMarkers(): Promise<IDBDatabase> {
  if (connection) return Promise.resolve(connection)
  if (opening) return opening
  opening = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1)
    let blocked = false
    request.onupgradeneeded = () => request.result.createObjectStore(markerStore)
    request.onerror = () => reject(request.error)
    request.onblocked = () => {
      blocked = true
      reject(new Error("交易记录存储暂不可用"))
    }
    request.onsuccess = () => {
      const database = request.result
      if (blocked) { database.close(); return }
      const forget = () => { if (connection === database) connection = null }
      database.onclose = forget
      database.onversionchange = () => { forget(); database.close() }
      connection = database
      resolve(database)
    }
  }).finally(() => {
    opening = null
  })
  return opening
}

function pendingId(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= 64 && value.trim() === value ? value : null
}

export function updateFundMarker<T>(key: string, update: (id: string | null) => { id: string | null; value: T }): Promise<T> {
  const database = connection
  if (!database) {
    // Reject this confirmation. Waiting and requesting a transaction later could pick up a new number.
    void openFundMarkers().catch(() => {})
    return Promise.reject(new Error("交易记录存储尚未就绪"))
  }
  return new Promise<T>((resolve, reject) => {
    const transaction = database.transaction(markerStore, "readwrite")
    const markers = transaction.objectStore(markerStore)
    let value: T
    transaction.oncomplete = () => resolve(value)
    transaction.onabort = () => reject(transaction.error)
    const read = markers.get(key)
    read.onsuccess = () => {
      const current = pendingId(read.result)
      try {
        const next = update(current)
        value = next.value
        if (next.id !== current) {
          if (next.id) markers.put(next.id, key)
          else markers.delete(key)
        }
      } catch (cause) {
        reject(cause)
        transaction.abort()
      }
    }
  })
}

export async function readFundMarkers(prefix: string): Promise<Map<string, string>> {
  const database = await openFundMarkers()
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(markerStore, "readonly")
    const result = new Map<string, string>()
    transaction.oncomplete = () => resolve(result)
    transaction.onabort = () => reject(transaction.error)
    const range = IDBKeyRange.bound(prefix, `${prefix}\uffff`, false, true)
    const request = transaction.objectStore(markerStore).openCursor(range)
    request.onsuccess = () => {
      const cursor = request.result
      if (!cursor) return
      const id = pendingId(cursor.value)
      if (typeof cursor.key === "string" && id) result.set(cursor.key, id)
      cursor.continue()
    }
  })
}
