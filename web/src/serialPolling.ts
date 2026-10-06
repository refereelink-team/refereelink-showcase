type Timers = {
  set: (work: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clear: (id: ReturnType<typeof setTimeout>) => void;
};

// Slow responses settle before another poll begins. Disposing also prevents a
// still-pending response from scheduling work for an unmounted page.
export function serialPolling(
  refresh: () => Promise<unknown>,
  delay: number,
  timers: Timers = { set: setTimeout, clear: clearTimeout },
) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function poll() {
    try {
      await refresh();
    } catch {
      // Resource refresh reports its own error; a transient failure still retries.
    }
    if (!stopped) timer = timers.set(() => void poll(), delay);
  }
  void poll();
  return () => {
    stopped = true;
    if (timer !== undefined) timers.clear(timer);
  };
}
