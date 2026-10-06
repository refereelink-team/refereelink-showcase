import { useEffect, useState } from 'react';

// At most one image request is active. Keep the latest image and release old blobs.
export default function LivePreview({
  url,
  alt,
  active = true,
}: {
  url: string;
  alt: string;
  active?: boolean;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let current: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    async function refresh() {
      if (stopped) return;
      if (document.visibilityState === 'hidden') {
        timer = setTimeout(refresh, 500);
        return;
      }
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 2000);
      try {
        const response = await fetch(url, { cache: 'no-store', signal: request.signal });
        if (!response.ok) throw new Error('Preview unavailable');
        const blob = await response.blob();
        if (stopped) return;
        const next = URL.createObjectURL(blob);
        if (current) URL.revokeObjectURL(current);
        current = next;
        setImage(next);
        setFailed(false);
      } catch {
        if (!stopped) {
          setFailed(true);
          setImage(null);
        }
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(refresh, 500);
      }
    }
    void refresh();
    return () => {
      stopped = true;
      clearTimeout(timer);
      request?.abort();
      if (current) URL.revokeObjectURL(current);
    };
  }, [url, active]);
  return (
    <>
      {image && active && !failed ? (
        <img src={image} alt={alt} />
      ) : (
        <span role="status">{failed ? '预览暂时中断，正在重连…' : '正在读取现场画面…'}</span>
      )}
    </>
  );
}
