let fallbackSequence = 0;

export function randomUuid(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof cryptoApi?.getRandomValues === "function") {
    cryptoApi.getRandomValues(bytes);
  } else {
    const now = Date.now();
    fallbackSequence = (fallbackSequence + 1) % Number.MAX_SAFE_INTEGER;
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
    bytes[0] ^= now & 0xff;
    bytes[1] ^= (now / 256) & 0xff;
    bytes[2] ^= fallbackSequence & 0xff;
    bytes[3] ^= (fallbackSequence / 256) & 0xff;
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function requestId(prefix = "xa", separator = "-"): string {
  return `${prefix}${separator}${randomUuid()}`;
}
