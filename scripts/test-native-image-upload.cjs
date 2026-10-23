const test = require('node:test');
const assert = require('node:assert/strict');
const { prepareImage } = require('../apps/boss-miniapp/native/core/image-upload');

function environment(sizes) {
  const calls = [];
  global.wx = {
    getFileSystemManager: () => ({ getFileInfo: options => options.success({size:sizes[options.filePath]}) }),
    getImageInfo: options => options.success({width:3000,height:6000}),
    compressImage: options => { calls.push(options); options.success({tempFilePath:'compressed-'+calls.length}); },
  };
  return calls;
}
test('an image already at the 200000-byte boundary needs no recompression', async () => {
  const calls = environment({source:200000});
  assert.equal(await prepareImage('source'),'source');
  assert.equal(calls.length,0);
});
test('compression measures each result and preserves the source aspect ratio', async () => {
  const calls = environment({source:1000000,'compressed-1':250000,'compressed-2':180000});
  assert.equal(await prepareImage('source'),'compressed-2');
  assert.equal(calls.length,2);
  assert.equal(calls[0].compressedWidth,800);
  assert.equal(calls[0].compressedHeight,1600);
  assert.ok(calls[1].quality < calls[0].quality);
});
test('a PNG insensitive to quality is retried at a smaller resolution', async () => {
  const sizes={source:1000000};for(let i=1;i<=4;i++)sizes['compressed-'+i]=i===4?190000:300000;
  const calls=environment(sizes);
  assert.equal(await prepareImage('source'),'compressed-4');
  assert.equal(calls[3].compressedHeight,1200);
});
test('an unreadable image is rejected before upload', async () => {
  environment({source:0});
  await assert.rejects(prepareImage('source'),/读取图片大小/);
});
test('a failed compressor never falls back to the oversized original', async () => {
  environment({source:1000000});wx.compressImage=options=>options.fail();
  await assert.rejects(prepareImage('source'),/压缩失败/);
});
test('an image that remains oversized is rejected after bounded retries', async () => {
  const sizes={source:1000000};for(let i=1;i<=18;i++)sizes['compressed-'+i]=300000;
  const calls=environment(sizes);
  await assert.rejects(prepareImage('source'),/仍超过200KB/);
  assert.equal(calls.length,18);
});
