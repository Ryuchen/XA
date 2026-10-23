import React, { useEffect, useRef, useState } from 'react';
import { Image } from '@tarojs/components';
import { renewPrivateImage } from '@/utils/privateMedia';

export default function PrivateImage({ src, ...props }: React.ComponentProps<typeof Image>) {
  const [url, setUrl] = useState(src);
  const generation = useRef(0);
  const attempted = useRef(false);
  useEffect(() => {
    setUrl(src); attempted.current = false; ++generation.current;
    return () => { ++generation.current; };
  }, [src]);
  const renew = async () => {
    if (!src || attempted.current) return;
    attempted.current = true;
    const version = generation.current;
    try {
      const next = await renewPrivateImage(src);
      if (version === generation.current) setUrl(next);
    } catch { /* A preview click can retry and displays the API error. */ }
  };
  return <Image {...props} src={url} onError={renew} />;
}
