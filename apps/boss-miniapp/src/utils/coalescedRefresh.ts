/** Merge push bursts; changes received during a request get one trailing refresh. */
export function createCoalescedRefresh(refresh: () => Promise<unknown>, delayMs = 300) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending = false;
  let running = false;
  let disposed = false;

  const schedule = () => {
    if (disposed || running || timer !== null || !pending) return;
    // Spread subscribers over 200 ms so one broadcast does not hit MySQL at once.
    timer = setTimeout(async () => {
      timer = null;
      if (disposed) return;
      pending = false;
      running = true;
      try {
        await refresh();
      } catch {
        // Page loaders display their own errors; a rejection must release the queue.
      } finally {
        running = false;
        schedule();
      }
    }, delayMs + Math.floor(Math.random() * 200));
  };

  return {
    request() {
      if (disposed) return;
      pending = true;
      schedule();
    },
    dispose() {
      disposed = true;
      pending = false;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}

/** Public pool broadcasts contain only id/status; owned orders carry the serializer fields. */
export function isPersonalOrderUpdate(data: unknown): boolean {
  return !!data && typeof data === 'object'
    && Object.prototype.hasOwnProperty.call(data, 'provider');
}
