// Reproduce the packaged 650 ms bell used by the provider miniapp.
const fs = require('node:fs');
const path = require('node:path');
const sampleRate = 22050, duration = 0.65;
const samples = Math.round(sampleRate * duration);
const wav = Buffer.alloc(44 + samples * 2);
wav.write('RIFF', 0);
wav.writeUInt32LE(wav.length - 8, 4);
wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(sampleRate, 24);
wav.writeUInt32LE(sampleRate * 2, 28);
wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34);
wav.write('data', 36);
wav.writeUInt32LE(samples * 2, 40);
const partials = [[880, 0.28], [1320, 0.12], [1760, 0.06]];
for (let i = 0; i < samples; i++) {
  const time = i / sampleRate;
  const value = partials.reduce((sum, [frequency, volume]) => {
    const envelope = time < 0.008 ? volume * time / 0.008
      : volume * Math.pow(0.0001 / volume, (time - 0.008) / (duration - 0.008));
    return sum + Math.sin(2 * Math.PI * frequency * time) * envelope;
  }, 0);
  wav.writeInt16LE(Math.round(value * 32767), 44 + i * 2);
}
const target = path.resolve(__dirname, '../apps/provider-miniapp/src/assets/new-order-bell.wav');
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.writeFileSync(target, wav);
console.log(`Generated ${path.basename(target)}: ${duration}s, ${wav.length} bytes`);
